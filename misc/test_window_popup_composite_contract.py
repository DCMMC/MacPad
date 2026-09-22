"""Source boundary checks, complementary to native screenshot acceptance."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
DISPLAY = (ROOT / "macwsdisplayd/main.m").read_text()
VIEW = (ROOT / "MacWSHost/Rendering/MacWSMetalView.m").read_text()
INPUT = (ROOT / "libmachook/AppInputBridge.m").read_text()


class PopupAndProbeBoundaries(unittest.TestCase):
    def test_popup_keeps_isolated_surface_for_fallback(self):
        publish = DISPLAY.split("static void PublishFrame(", 1)[1]
        self.assertLess(publish.index("layer.latestSurface = surface"),
                        publish.index("surface = FinalCompositeSurface"))
        self.assertIn("client.mode == MacWSStreamModeWindow && layer.popupCompositeEligible", publish)

    def test_popup_requires_real_completion_and_leases(self):
        self.assertIn("FinalCompositeRecord.completionTime >= layer.popupCompositeMinimumTime", DISPLAY)
        self.assertIn("if (finalComposite || nativePopup) {\n        IOSurfaceIncrementUseCount(surface)", DISPLAY)
        self.assertIn("if (outstanding >= 3)", DISPLAY)

    def test_visible_part_of_tall_context_menu_is_supported(self):
        policy = DISPLAY.split("static void ConfigureNativePopupComposite(", 1)[1].split("static void ReconcileTransientStreams", 1)[0]
        self.assertIn("!CGRectIntersectsRect(baseBounds, popupBounds)", policy)
        self.assertNotIn("!CGRectContainsRect(baseBounds, popupBounds)", policy)
        self.assertIn("CGRectIntersectsRect(other, paintBounds)", policy)
        self.assertIn("number == layer.windowID", policy)

    def test_shadow_sampling_does_not_change_input_descriptor(self):
        self.assertIn("CGRect paintDestination = destination", VIEW)
        self.assertIn("CGRectIntersection(paintDestination, visiblePixels)", VIEW)
        self.assertIn("!nativePopup && _shadowPipeline", VIEW)
        self.assertIn("overlay.contentX +", VIEW)

    def test_cancel_is_only_for_owned_export_probe(self):
        cancel = INPUT.split("if (exactContinuation && record.kind == MacWSInputKindTouchCancel &&", 1)[1]
        self.assertTrue(cancel.lstrip().startswith("record.source == MacWSInputSourceInteropDragProbe)"))
        self.assertLess(cancel.index("postCancelKey(27, 53, true)"),
                        cancel.index("firstResult = postMouse"))
        self.assertIn("postCancelKey(27, 53, false)", cancel)

    def test_packed_metrics_are_byte_containers(self):
        self.assertNotIn("valueWithBytes:&entry", DISPLAY)
        self.assertNotIn("getValue:&metrics", DISPLAY)
        self.assertIn("dataWithBytes:&entry length:sizeof(entry)", DISPLAY)

    def test_only_diagnostic_sidecar_is_rate_limited(self):
        status = DISPLAY.split("static void WriteFinalCompositeState(", 1)[1].split("@interface", 1)[0]
        self.assertIn("lastProducer != producerPID", status)
        self.assertLess(status.index("if (!changed && now - lastAttempt < 5.0) return"),
                        status.index("NSString *payload"))
        delivery = DISPLAY.split("static void DeliverFinalComposite(", 1)[1].split("static CGDisplayStreamRef", 1)[0]
        self.assertIn("PublishFrame(client, nil, record.completionTime", delivery)

    def test_indirect_pointer_can_dismiss_popup_without_click_through(self):
        self.assertIn(
            "record.kind == MacWSInputKindTouchDown &&\n"
            "            record.source == MacWSInputSourceIndirectPointer",
            INPUT,
        )
        self.assertIn("MacWSPopupDismissPointerActive = YES", INPUT)
        self.assertIn(
            "record.contactID == MacWSPopupDismissPointerContact", INPUT)
        self.assertIn(
            "record.sceneID == MacWSPopupDismissPointerScene", INPUT)
        self.assertLess(
            INPUT.index("if (matchingTerminal)"),
            INPUT.index("Class screenClass = objc_getClass(\"NSScreen\")"),
        )


if __name__ == "__main__":
    unittest.main()
