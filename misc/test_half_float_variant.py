import pathlib
import sys
import unittest


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import build_half_float_metal_variant as builder
import repack_metallib_macabi as repack


class HalfFloatVariantAnalysisTests(unittest.TestCase):
    def test_named_texture_argument_resolves_by_formal_position(self):
        assembly = r'''
define void @named(%struct._texture_2d_t addrspace(1)* nocapture %output.coerce, <2 x i32> %position) #0 {
entry:
  tail call void @air.write_texture_2d.v4f32(%struct._texture_2d_t addrspace(1)* nocapture %output.coerce, <2 x i32> %position, <4 x float> zeroinitializer, i32 0, i32 2) #1
  ret void
}
!16 = !{i32 0, !"air.texture", !"air.location_index", i32 3, i32 1, !"air.write"}
'''
        self.assertEqual(
            builder.writable_texture_locations(assembly, "named"), [3]
        )

    def test_numbered_texture_argument_remains_supported(self):
        assembly = r'''
define void @numbered(i32 %0, %struct._texture_2d_t addrspace(1)* %1) #0 {
entry:
  call void @air.write_texture_2d.v4f32(%struct._texture_2d_t addrspace(1)* %1, <2 x i32> zeroinitializer, <4 x float> zeroinitializer, i32 0, i32 2) #1
  ret void
}
!9 = !{i32 1, !"air.texture", !"air.location_index", i32 7, i32 1, !"air.write"}
'''
        self.assertEqual(
            builder.writable_texture_locations(assembly, "numbered"), [7]
        )

    def test_indirect_texture_pointer_fails_closed(self):
        assembly = r'''
define void @indirect(%struct._texture_2d_t addrspace(1)* %0) #0 {
entry:
  %derived = getelementptr i8, %struct._texture_2d_t addrspace(1)* %0, i64 0
  call void @air.write_texture_2d.v4f32(%struct._texture_2d_t addrspace(1)* %derived, <2 x i32> zeroinitializer, <4 x float> zeroinitializer, i32 0, i32 2) #1
  ret void
}
!4 = !{i32 0, !"air.texture", !"air.location_index", i32 0, i32 1, !"air.write"}
'''
        with self.assertRaisesRegex(ValueError, "do not resolve to direct"):
            builder.writable_texture_locations(assembly, "indirect")

    def test_constant_vector_write_gets_scalar_clamps_and_new_attribute(self):
        assembly = r'''
define void @constant(%struct._texture_2d_t addrspace(1)* %output) #0 {
entry:
  tail call void @air.write_texture_2d.v4f32(%struct._texture_2d_t addrspace(1)* %output, <2 x i32> zeroinitializer, <4 x float> <float 1.000000e+05, float -1.000000e+05, float 1.000000e+00, float 0.000000e+00>, i32 0, i32 2) #1
  ret void
}

declare void @air.write_texture_2d.v4f32(%struct._texture_2d_t addrspace(1)*, <2 x i32>, <4 x float>, i32, i32) #1

attributes #0 = { nounwind }
attributes #1 = { argmemonly nounwind }
'''
        rewritten = repack.saturate_half_float_texture_writes(
            assembly, "constant"
        )
        self.assertEqual(rewritten.count("tail call fast float"), 4)
        self.assertIn(
            "attributes #2 = { nounwind readnone }", rewritten
        )
        self.assertIn("@air.fast_clamp.f32", rewritten)
        self.assertNotIn(
            "<4 x float> <float 1.000000e+05, float -1.000000e+05",
            rewritten.split("@air.write_texture_2d.v4f32", 2)[-1],
        )


if __name__ == "__main__":
    unittest.main()
