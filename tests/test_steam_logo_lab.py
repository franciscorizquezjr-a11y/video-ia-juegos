import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

from PIL import Image
import steam_logo_lab as lab


def setUpModule():
    # temp/ is gitignored, so a fresh checkout does not have it yet.
    (lab.ROOT / "temp").mkdir(parents=True, exist_ok=True)


class SteamLogoLabTests(unittest.TestCase):
    def test_app_id_accepts_only_ids_and_steam_store_urls(self):
        self.assertEqual(lab.parse_app_id("620"), 620)
        self.assertEqual(lab.parse_app_id("https://store.steampowered.com/app/620/Portal_2/?l=es"), 620)
        for value in ("0", "-1", "Portal 2", "https://example.com/app/620", "https://store.steampowered.com.evil/app/620"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                lab.parse_app_id(value)

    def test_detection_coordinates_are_y_first_and_clipped(self):
        self.assertEqual(lab.pixel_box([100, 200, 500, 800], (1000, 500), padding=0), (200, 50, 800, 250))
        self.assertEqual(lab.pixel_box([0, 0, 1000, 1000], (1000, 500)), (0, 0, 1000, 500))
        for value in (None, [1, 2], [0, 0, 0, 1], [-1, 0, 1000, 1000], [0, 0, math.nan, 1000], [0, False, 1000, 1000]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                lab.pixel_box(value, (1000, 500))

    def test_extraction_preserves_png_alpha_and_original_coordinates(self):
        capsule = Image.new("RGB", (1000, 500), "blue")
        logo = Image.new("RGBA", (400, 100), (255, 255, 255, 0))
        logo.putpixel((20, 20), (255, 255, 255, 255))
        response = Mock()
        response.json.return_value = {"620": {"success": True, "data": {
            "name": "Portal 2", "capsule_image": "https://shared.akamai.steamstatic.com/capsule.jpg"}}}
        with tempfile.TemporaryDirectory(dir=lab.ROOT / "temp") as temp, patch.object(lab, "RESULTS", Path(temp)), \
             patch.object(lab.requests, "get", return_value=response), \
             patch.object(lab, "fetch_main_capsule", return_value=(capsule, "https://shared.akamai.steamstatic.com/capsule.jpg", "main")), \
             patch.object(lab, "detect_logo", return_value={"found": True, "method": "opencv-mser", "bounds": [188, 46, 812, 254]}), \
             patch.object(lab, "remove_flat_background", return_value=logo) as remove, \
             patch.object(lab, "remove_rembg_background", return_value=logo), \
             patch.object(lab, "remove_ben2_background", return_value=logo), \
             patch.object(lab, "remove_grabcut_background", return_value=logo):
            progress = Mock()
            result = lab.extract_logo(620, progress)
            folder = Path(temp) / result["run_id"]
            self.assertEqual(remove.call_args.args[0].size, (624, 208))
            self.assertEqual(result["crop_bounds"], [188, 46, 812, 254])
            self.assertEqual(len(result["algorithms"]), 4)
            self.assertEqual(result["success_count"], 4)
            with Image.open(folder / "logo-color.png") as output:
                self.assertEqual(output.mode, "RGBA")
                self.assertEqual(output.getchannel("A").getextrema(), (0, 255))
            self.assertEqual(json.loads((folder / "result.json").read_text(encoding="utf-8")), result)
            self.assertEqual(progress.call_count, 10)

    def test_image_download_rejects_non_steam_hosts_without_request(self):
        with patch.object(lab.requests, "get") as get:
            with self.assertRaises(ValueError):
                lab.download_image("https://localhost/capsule.png")
            get.assert_not_called()

    def test_main_capsule_404_uses_explicit_header_fallback(self):
        import requests
        response = Mock(status_code=404)
        missing = requests.HTTPError(response=response)
        image = Image.new("RGB", (920, 430))
        with patch.object(lab, "download_image", side_effect=[missing, missing, image]) as download:
            result = lab.fetch_main_capsule(620, {"header_image": "https://shared.akamai.steamstatic.com/header.jpg"})
            self.assertEqual(result[2], "header_fallback")
            self.assertIn("capsule_616x353", download.call_args_list[0].args[0])
            self.assertEqual(download.call_count, 3)

    def test_main_capsule_network_errors_are_not_hidden_as_fallback(self):
        import requests
        with patch.object(lab, "download_image", side_effect=requests.Timeout):
            with self.assertRaises(requests.Timeout):
                lab.fetch_main_capsule(620, {})

    def test_background_removal_rejects_empty_or_opaque_result(self):
        import sys
        module = Mock()
        module.remove.return_value = Image.new("RGBA", (20, 20))
        with patch.dict(sys.modules, {"rembg": module}), patch.object(lab, "_rembg_session", Mock()):
            with self.assertRaises(ValueError):
                lab.remove_background(Image.new("RGB", (20, 20)))
            module.remove.return_value = Image.new("RGBA", (20, 20), "white")
            with self.assertRaises(ValueError):
                lab.remove_background(Image.new("RGB", (20, 20)))
            image = Image.new("RGBA", (20, 20))
            image.paste((255, 255, 255, 255), (5, 5, 15, 15))
            module.remove.return_value = image
            output = lab.remove_background(Image.new("RGB", (20, 20)))
            self.assertEqual(output.size, (26, 26))
            self.assertEqual(output.getpixel((8, 8)), (255, 255, 255, 255))

    def test_flat_background_removal_clears_inside_letter_holes(self):
        from PIL import ImageDraw
        image = Image.new("RGB", (100, 100), (245, 245, 245))
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 80, 80), fill=(70, 70, 70))
        draw.rectangle((35, 35, 65, 65), fill=(245, 245, 245))
        result = lab.remove_flat_background(image)
        self.assertIsNotNone(result)
        self.assertEqual(result.getpixel((0, 0))[3], 0)
        self.assertEqual(result.getpixel((50, 50))[3], 0)
        self.assertEqual(result.getpixel((25, 25))[3], 255)
        with patch.object(lab, "_rembg_session", None):
            output = lab.remove_background(image)
            self.assertEqual(output.info["background_method"], "flat-color-matte")

    def test_complex_background_uses_saliency_instead_of_color_matte(self):
        image = Image.new("RGB", (100, 100), "red")
        image.paste("blue", (50, 0, 100, 100))
        self.assertIsNone(lab.remove_flat_background(image))

    def test_detector_finds_light_dark_and_multiline_titles_without_llm(self):
        import cv2
        import numpy as np
        for foreground, background in ((0, 255), (255, 0)):
            with self.subTest(foreground=foreground):
                image = np.full((300, 600, 3), background, np.uint8)
                cv2.putText(image, "PORTAL", (110, 150), cv2.FONT_HERSHEY_SIMPLEX, 2, (foreground,)*3, 4)
                cv2.putText(image, "SECOND CHAPTER", (140, 200), cv2.FONT_HERSHEY_SIMPLEX, .7, (foreground,)*3, 2)
                result = lab.detect_logo(Image.fromarray(image))
                self.assertTrue(result["found"])
                x1, y1, x2, y2 = result["bounds"]
                self.assertLessEqual(x1, 112)
                self.assertLessEqual(y1, 110)
                self.assertGreaterEqual(x2, 330)
                self.assertGreaterEqual(y2, 200)
                self.assertEqual(result["method"], "opencv-mser")
        blank = lab.detect_logo(Image.new("RGB", (600, 300), "white"))
        self.assertFalse(blank["found"])
        self.assertTrue(blank["needs_review"])
        self.assertEqual(blank["bounds"], [0, 0, 600, 300])

    def test_detector_converts_downscaled_coordinates_to_original_pixels(self):
        import numpy as np
        regions = [{"box": (100, 50, 125, 100), "contrast": 90, "stroke": 3, "region": np.ones((50, 25))},
                   {"box": (130, 50, 155, 100), "contrast": 90, "stroke": 3, "region": np.ones((50, 25))}]
        with patch.object(lab, "_cv_regions", return_value=(regions, .5, (400, 200))):
            result = lab.detect_logo(Image.new("RGB", (800, 400)))
            self.assertEqual(result["bounds"], [192, 88, 318, 212])

    def test_grabcut_uses_context_and_clears_background(self):
        from PIL import ImageDraw
        image = Image.new("RGB", (200, 100), "white")
        ImageDraw.Draw(image).rectangle((60, 30, 140, 70), fill="black")
        result = lab.remove_grabcut_background(image, [40, 15, 160, 85])
        self.assertEqual(result.size, (120, 70))
        self.assertEqual(result.getpixel((60, 35))[3], 255)
        self.assertEqual(result.getpixel((5, 5))[3], 0)

    def test_grabcut_does_not_seed_white_letter_holes_as_foreground(self):
        from PIL import ImageDraw
        image = Image.new("RGB", (200, 120), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((60, 30, 140, 90), fill="black")
        draw.rectangle((80, 50, 120, 70), fill="white")
        result = lab.remove_grabcut_background(image, [40, 15, 160, 105])
        self.assertEqual(result.getpixel((25, 30))[3], 255)
        self.assertEqual(result.getpixel((60, 45))[3], 0)

    def test_ben2_reuses_model_and_script_configuration(self):
        import sys
        torch = MagicMock()
        torch.cuda.is_available.return_value = False
        ben = MagicMock()
        model = ben.BEN_Base.from_pretrained.return_value
        model.inference.return_value = Image.new("RGBA", (20, 20))
        with patch.dict(sys.modules, {"ben2": ben, "torch": torch}), \
             patch.object(lab, "_ben_model", None), patch.object(lab, "_ben_device", None):
            for _ in range(2):
                output = lab.remove_ben2_background(Image.new("RGB", (20, 20)))
                self.assertEqual(output.info["device"], "cpu")
            ben.BEN_Base.from_pretrained.assert_called_once()
            model.to.assert_called_once_with("cpu")
            self.assertEqual(model.inference.call_count, 2)
            self.assertIs(model.inference.call_args.kwargs["refine_foreground"], False)

    def test_partial_failures_and_manual_recalculation_preserve_previous_run(self):
        from PIL import ImageDraw
        image = Image.new("RGB", (100, 100), "white")
        logo = Image.new("RGBA", (80, 80))
        ImageDraw.Draw(logo).rectangle((20, 20, 60, 60), fill="black")
        with tempfile.TemporaryDirectory(dir=lab.ROOT / "temp") as temp, patch.object(lab, "RESULTS", Path(temp)), \
             patch.object(lab, "remove_flat_background", return_value=None), \
             patch.object(lab, "remove_rembg_background", return_value=logo) as rembg, \
             patch.object(lab, "remove_ben2_background", side_effect=RuntimeError("BEN unavailable")), \
             patch.object(lab, "remove_grabcut_background", return_value=logo), \
             patch.object(lab.requests, "get") as get:
            result = lab.compare_algorithms(image, {"app_id": 620, "name": "Portal 2"}, [10, 10, 90, 90], {"method": "opencv-mser"}, Mock())
            self.assertEqual([a["status"] for a in result["algorithms"]], ["not_applicable", "completed", "error", "completed"])
            self.assertEqual(result["success_count"], 2)
            original = (Path(temp)/result["run_id"]/"result.json").read_bytes()
            recalculated = lab.recalculate_logo(result["run_id"], [20, 20, 70, 80], Mock())
            self.assertNotEqual(recalculated["run_id"], result["run_id"])
            self.assertEqual(recalculated["parent_run_id"], result["run_id"])
            self.assertEqual(recalculated["crop_bounds"], [20, 20, 70, 80])
            self.assertEqual(recalculated["detector"]["method"], "manual")
            self.assertEqual((Path(temp)/result["run_id"]/"result.json").read_bytes(), original)
            get.assert_not_called()
            self.assertEqual(rembg.call_args.args[0].size, (50, 60))

    def test_bounds_reject_out_of_range_small_and_non_integer_crops(self):
        for bounds in ([0, 0, 101, 100], [0, 0, 1, 1], [-1, 0, 80, 90], [0., 0, 50, 50], [False, 0, 50, 50]):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                lab.validate_bounds(bounds, (100, 100))


class SteamLogoLabApiTests(unittest.TestCase):
    def test_uploaded_crop_skips_steam_and_detection(self):
        from io import BytesIO
        data = BytesIO()
        Image.new("RGB", (80, 35), "white").save(data, "PNG")
        class InlineThread:
            def __init__(self, target, **kwargs):
                self.target = target
            def start(self):
                self.target()
        with patch.object(lab.threading, "Thread", InlineThread), \
             patch.object(lab, "compare_algorithms", return_value={"success_count": 4}) as compare, \
             patch.object(lab, "detect_logo") as detect, patch.object(lab.requests, "get") as network:
            response = self.client.post("/api/logo-lab/upload", files={"file": ("logo.png", data.getvalue(), "image/png")})
            self.assertEqual(response.status_code, 200)
            image, metadata, bounds, detection, _ = compare.call_args.args
            self.assertEqual(image.size, (80, 35))
            self.assertEqual(bounds, [0, 0, 80, 35])
            self.assertEqual(metadata["source_kind"], "upload")
            self.assertEqual(detection["seed_boxes"], [])
            detect.assert_not_called()
            network.assert_not_called()
        self.assertEqual(self.client.post("/api/logo-lab/upload", files={"file": ("bad.png", b"invalid")}).status_code, 400)
        self.assertEqual(self.client.post("/api/logo-lab/upload", files={"file": ("huge.png", b"x"*(20*1024*1024+1))}).status_code, 413)

    def setUp(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI/httpx no disponibles")
        self.jobs = []

        def create_job(kind):
            job = Mock(job_id="steam_logo_test", job_type=kind)
            self.jobs.append(job)
            return job

        app = FastAPI()
        lab.register_routes(app, create_job)
        self.client = TestClient(app)

    def test_invalid_input_never_starts_job(self):
        self.assertEqual(self.client.post("/api/logo-lab/extract", json={"steam_input": "https://evil/app/620"}).status_code, 400)
        self.assertEqual(self.client.post("/api/logo-lab/extract", json={"steam_input": ""}).status_code, 422)
        self.assertFalse(self.jobs)

    def test_worker_completes_and_reports_errors(self):
        import threading
        finished = threading.Event()
        result = {"name": "Portal 2", "success_count": 4}

        def extract(*args):
            finished.set()
            return result

        # Run the worker synchronously here so terminal status is deterministic.
        class InlineThread:
            def __init__(self, target, **kwargs):
                self.target = target
            def start(self):
                self.target()

        with patch.object(lab.threading, "Thread", InlineThread), patch.object(lab, "extract_logo", side_effect=extract):
            response = self.client.post("/api/logo-lab/extract", json={"steam_input": "620"})
            self.assertEqual(response.json(), {"job_id": "steam_logo_test"})
            self.jobs[-1].complete.assert_called_once_with(result, message="Comparación terminada: 4/4 PNG generados.")
        with patch.object(lab.threading, "Thread", InlineThread), patch.object(lab, "extract_logo", side_effect=ValueError("No logo")):
            self.client.post("/api/logo-lab/extract", json={"steam_input": "620"})
            self.jobs[-1].fail.assert_called_once_with("No logo")

    def test_saved_png_download_and_asset_limits(self):
        run_id = "a" * 32
        with tempfile.TemporaryDirectory(dir=lab.ROOT / "temp") as temp, patch.object(lab, "RESULTS", Path(temp)):
            folder = Path(temp) / run_id
            folder.mkdir()
            Image.new("RGBA", (20, 20)).save(folder / "logo.png")
            for algorithm, _ in lab.ALGORITHMS:
                Image.new("RGBA", (20, 20)).save(folder / f"logo-{algorithm}.png")
            response = self.client.get(f"/api/logo-lab/results/{run_id}/download")
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response.headers["content-disposition"])
            self.assertEqual(response.headers["content-type"], "image/png")
            self.assertEqual(self.client.get(f"/api/logo-lab/results/{run_id}/secret.txt").status_code, 404)
            self.assertEqual(self.client.get("/api/logo-lab/results/invalid/logo.png").status_code, 404)
            for algorithm, _ in lab.ALGORITHMS:
                response = self.client.get(f"/api/logo-lab/results/{run_id}/download-{algorithm}")
                self.assertEqual(response.status_code, 200)
                self.assertIn(f"logo-{algorithm}", response.headers["content-disposition"])

    def test_recalculation_validation_and_metadata_compatibility(self):
        run_id = "b"*32
        with tempfile.TemporaryDirectory(dir=lab.ROOT / "temp") as temp, patch.object(lab, "RESULTS", Path(temp)):
            folder = Path(temp)/run_id
            folder.mkdir()
            legacy = {"run_id": run_id, "app_id": 620, "name": "Portal 2", "logo_url": "/old-logo.png"}
            (folder/"result.json").write_text(json.dumps(legacy), encoding="utf-8")
            Image.new("RGB", (100, 100)).save(folder/"capsule.png")
            self.assertEqual(self.client.get(f"/api/logo-lab/results/{run_id}").json(), legacy)
            for bounds, code in (([0, 0, 101, 100], 400), ([0., 0, 80, 80], 422), ([0, 0, 1, 1], 400)):
                response = self.client.post("/api/logo-lab/recalculate", json={"run_id": run_id, "crop_bounds": bounds})
                self.assertEqual(response.status_code, code)
            self.assertFalse(self.jobs)
            response = self.client.post("/api/logo-lab/recalculate", json={"run_id": "c"*32, "crop_bounds": [0, 0, 80, 80]})
            self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
