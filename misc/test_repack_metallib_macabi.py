import importlib.util
import pathlib
import struct
import sys
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("repack_metallib_macabi.py")
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("repack_metallib_macabi", MODULE_PATH)
assert SPEC and SPEC.loader
REPACK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPACK)


class AirAbiAnalysisTests(unittest.TestCase):
    def test_analysis_reports_contract_vocabulary_without_function_names(self):
        text = """\
target datalayout = "e-p:64:64-i64:64-n32-S64"
target triple = "air64-apple-macosx13.0.0"
declare float @air.floor.f32(float)
declare void @llvm.lifetime.start.p0(i64, ptr nocapture)
!0 = !{i32 0, !"air.buffer", !"air.location_index", i32 3}
!air.fragment = !{!0}
"""
        report = REPACK.analyze_air_assembly(text, "arbitrary-name")
        self.assertEqual(report["target_triple"], "air64-apple-macosx13.0.0")
        self.assertEqual(
            report["datalayout"], "e-p:64:64-i64:64-n32-S64"
        )
        self.assertEqual(report["stage_metadata"], ["fragment"])
        self.assertEqual(
            report["abi_symbols"],
            ["air.floor.f32", "llvm.lifetime.start.p0"],
        )
        self.assertEqual(
            report["abi_metadata_keys"],
            ["air.buffer", "air.location_index"],
        )

    def test_versioned_air64_triple_and_mesh_stage_are_supported(self):
        text = """\
target triple = "air64_v28-apple-macosx26.0.0"
!air.mesh = !{!0}
"""
        report = REPACK.analyze_air_assembly(text, "mesh")
        self.assertEqual(
            report["target_triple"], "air64_v28-apple-macosx26.0.0"
        )
        self.assertEqual(report["stage_metadata"], ["mesh"])
        self.assertTrue(REPACK.target_triple_matches_container(
            "air64_v28-apple-ios26.0.0-macabi", "macabi"
        ))

    def test_function_record_inventory_reads_type_and_versions(self):
        def tag(name, payload):
            return name.encode("ascii") + struct.pack("<H", len(payload)) + payload

        tags = b"".join((
            tag("NAME", b"main0\0"),
            tag("TYPE", b"\x02"),
            tag("VERS", struct.pack("<HHHH", 2, 6, 3, 1)),
            tag("HASH", b"\0" * 32),
            tag("OFFT", b"\0" * 24),
            tag("MDSZ", struct.pack("<Q", 16)),
        )) + b"ENDT"
        group = struct.pack("<I", len(tags) + 4) + tags
        data = bytearray(REPACK.MTLB_HEADER_SIZE)
        struct.pack_into("<Q", data, 24, REPACK.MTLB_HEADER_SIZE)
        struct.pack_into("<Q", data, 32, len(group))
        data.extend(struct.pack("<I", 1))
        data.extend(group)
        data.extend(b"\0")

        [record] = REPACK.parse_function_records(data)
        self.assertEqual(record["function_type"], 2)
        self.assertEqual(record["function_type_name"], "kernel")
        self.assertEqual(record["air_version"], [2, 6])
        self.assertEqual(record["language_version"], [3, 1])

    def test_function_constants_use_stable_air_initializer_marker(self):
        text = """\
target triple = "air64-apple-macosx13.0.0"
@shader.MTL_FC_INIT_3_Dv4_j = internal addrspace(2) externally_initialized constant <4 x i32> undef, section "air.fc_initializer", align 16
@plain = internal addrspace(2) global i32 0
"""
        report = REPACK.analyze_air_assembly(text, "function-constants")
        self.assertEqual(report["function_constants"], [{
            "index": 3,
            "name": "shader",
            "type": "<4 x i32>",
            "abi_type": "Dv4_j",
        }])


class RuntimeManifestTests(unittest.TestCase):
    def test_manifest_is_versioned_and_verifies_both_artifacts(self):
        source = b"source metallib"
        output = b"output metallib"
        functions = [{
            "name": "frag",
            "selected": True,
            "function_type_name": "fragment",
            "function_constants": [{"index": 4}],
            "applied_lowerings": ["fract-v3f16"],
            "input_sha256": "11" * 32,
            "output_sha256": "22" * 32,
        }]
        manifest = REPACK.build_runtime_manifest(
            source=source,
            output=output,
            source_runtime_path="/System/source.metallib",
            output_runtime_path="/usr/local/share/output.metallib",
            profile="ventura13-ios19-macabi",
            function_reports=functions,
        )
        self.assertTrue(
            manifest["translated_functions"]["frag"]
            ["needs_function_constants"]
        )
        self.assertEqual(manifest["translation"], {
            "selection_policy": "all-functions",
            "source_function_count": 1,
            "translated_function_count": 1,
            "complete": True,
        })
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source_path = root / "source.metallib"
            output_path = root / "output.metallib"
            manifest_path = root / "route.plist"
            source_path.write_bytes(source)
            output_path.write_bytes(output)
            REPACK.write_runtime_manifest(manifest_path, manifest)
            verified = REPACK.verify_runtime_manifest(
                manifest_path, source_path, output_path
            )
            self.assertEqual(verified["profile"], "ventura13-ios19-macabi")
            output_path.write_bytes(output + b"tampered")
            with self.assertRaisesRegex(ValueError, "output size mismatch"):
                REPACK.verify_runtime_manifest(
                    manifest_path, source_path, output_path
                )

    def test_runtime_manifest_refuses_partial_shader_selection(self):
        functions = [
            {
                "name": "translated",
                "selected": True,
                "function_type_name": "kernel",
                "function_constants": [],
                "applied_lowerings": [],
                "input_sha256": "11" * 32,
                "output_sha256": "22" * 32,
            },
            {
                "name": "silently-missed",
                "selected": False,
                "function_type_name": "kernel",
                "function_constants": [],
                "applied_lowerings": [],
                "input_sha256": "33" * 32,
                "output_sha256": "33" * 32,
            },
        ]
        with self.assertRaisesRegex(
            ValueError, "complete-library translation"
        ):
            REPACK.build_runtime_manifest(
                source=b"source",
                output=b"output",
                source_runtime_path="/System/source.metallib",
                output_runtime_path="/usr/local/share/output.metallib",
                profile="ventura13-ios19-macabi",
                function_reports=functions,
            )


class StructuralLoweringTests(unittest.TestCase):
    def test_auto_fract_lowering_handles_every_call_and_keeps_first_spelling(self):
        text = """\
  %a = tail call fast <3 x half> @air.fract.v3f16(<3 x half> %x) #0
  %b = tail call fast <3 x half> @air.fract.v3f16(<3 x half> %y) #0
declare <3 x half> @air.fract.v3f16(<3 x half>) local_unnamed_addr #0
"""
        lowered, applied = REPACK.apply_air_lowerings(
            text, "not-name-keyed", set(), True
        )
        self.assertEqual(applied, ["fract-v3f16"])
        self.assertIn("%macws.fract.v3f16.floor =", lowered)
        self.assertIn("%macws.fract.v3f16.floor.1 =", lowered)
        self.assertNotIn("@air.fract.v3f16", lowered)
        self.assertEqual(lowered.count("@air.floor.v3f16"), 3)

    def test_auto_memset_lowering_handles_multiple_calls_without_ssa_collision(self):
        text = """\
  call void @llvm.memset.p0i8.i64(i8* nonnull align 8 dereferenceable(24) %p, i8 0, i64 24, i1 false)
  call void @llvm.memset.p0i8.i64(i8* nonnull align 8 dereferenceable(24) %q, i8 0, i64 24, i1 false)
declare void @llvm.memset.p0i8.i64(i8* nocapture writeonly, i8, i64, i1 immarg) #1
"""
        lowered, applied = REPACK.apply_air_lowerings(
            text, "any-function", set(), True
        )
        self.assertEqual(applied, ["zero-memset-24"])
        self.assertIn("%macws.memset.word.0 =", lowered)
        self.assertIn("%macws.memset.word.1.0 =", lowered)
        self.assertEqual(lowered.count("store i64 0"), 6)
        self.assertNotIn("@llvm.memset.p0i8.i64", lowered)

    def test_known_symbol_with_unknown_shape_refuses_instead_of_guessing(self):
        text = """\
  %a = call fast <3 x half> @air.fract.v3f16(<3 x half> %x) #0
declare <3 x half> @air.fract.v3f16(<3 x half>) local_unnamed_addr #0
"""
        with self.assertRaisesRegex(ValueError, "fract.v3f16"):
            REPACK.apply_air_lowerings(text, "unknown-shape", set(), True)

    def test_manual_and_auto_selection_do_not_apply_a_pass_twice(self):
        text = """\
  %a = tail call fast <3 x half> @air.fract.v3f16(<3 x half> %x) #0
declare <3 x half> @air.fract.v3f16(<3 x half>) local_unnamed_addr #0
"""
        lowered, applied = REPACK.apply_air_lowerings(
            text, "dedupe", {"fract-v3f16"}, True
        )
        self.assertEqual(applied, ["fract-v3f16"])
        self.assertEqual(lowered.count("@air.floor.v3f16"), 2)

    def test_single_call_auto_mode_is_identical_to_legacy_explicit_mode(self):
        text = """\
  %a = tail call fast <3 x half> @air.fract.v3f16(<3 x half> %x) #0
declare <3 x half> @air.fract.v3f16(<3 x half>) local_unnamed_addr #0
"""
        legacy, legacy_applied = REPACK.apply_air_lowerings(
            text, "fixed_frag_lph_cpf", {"fract-v3f16"}, False
        )
        automatic, automatic_applied = REPACK.apply_air_lowerings(
            text, "any-name", set(), True
        )
        self.assertEqual(automatic, legacy)
        self.assertEqual(automatic_applied, legacy_applied)


if __name__ == "__main__":
    unittest.main()
