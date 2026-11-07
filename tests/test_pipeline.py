"""End-to-end OCR test on a synthetic puzzle image (skipped if Tesseract is missing)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from puzzle_assistant.config import Config  # noqa: E402
from puzzle_assistant.ocr import find_tesseract  # noqa: E402

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


@unittest.skipUnless(find_tesseract(Config()) and HAVE_PIL, "Tesseract or Pillow not installed")
class PipelineTest(unittest.TestCase):
    def test_synthetic_puzzle(self):
        import cv2

        import make_synthetic
        from puzzle_assistant.analyzer import Analyzer
        from puzzle_assistant.detector import TitleDetector
        from puzzle_assistant.ocr import Tesseract
        from puzzle_assistant.vision import find_cards, find_panel, to_gray

        path = os.path.join(tempfile.mkdtemp(), "synthetic.png")
        make_synthetic.render(path)
        img = cv2.imread(path)
        cfg = Config()
        ocr = Tesseract(cfg)
        gray = to_gray(img)
        rect = TitleDetector(cfg, ocr).discover(gray)
        self.assertIsNotNone(rect, "title not found")
        panel = None
        for x, y, w, h in find_panel(gray, rect):
            if find_cards(gray[y:y + h, x:x + w]):
                panel = img[y:y + h, x:x + w]
                break
        self.assertIsNotNone(panel, "piece boxes not found")
        a = Analyzer(cfg, ocr).analyze_panel(panel)
        self.assertEqual(a.puzzle.target_caliber, "12")
        self.assertEqual(a.solution.excluded, ["F"])
        self.assertEqual(a.solution.order, list("HACDGBE"))
        self.assertTrue(a.solution.ok)


if __name__ == "__main__":
    unittest.main()
