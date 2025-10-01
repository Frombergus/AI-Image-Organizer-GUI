import unittest
import os
import tempfile
from unittest.mock import patch, MagicMock
from PIL import Image
import shutil
from src.extract_to_category import (
    encode_image_to_base64,
    classify_image_with_ollama,
    get_image_files,
    DEFAULT_CATEGORIES,
)

class TestExtractToCategory(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.backup_dir = os.path.join(self.test_dir, "backup_1")
        os.makedirs(self.backup_dir, exist_ok=True)
        self.images_dir = os.path.join(self.backup_dir, "images")
        os.makedirs(self.images_dir, exist_ok=True)
        
        # Create a test image
        test_img_path = os.path.join(self.images_dir, "test.jpg")
        Image.new("RGB", (800, 800)).save(test_img_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_encode_image_to_base64(self):
        img_path = os.path.join(self.images_dir, "test.jpg")
        base64_str = encode_image_to_base64(img_path)
        self.assertIsNotNone(base64_str)
        self.assertTrue(base64_str.startswith("data:image/png;base64,"))

    @patch("ollama.chat")
    def test_classify_image_with_ollama(self, mock_chat):
        mock_response = MagicMock()
        mock_response["message"]["content"] = "birthdays"
        mock_chat.return_value = mock_response
        
        img_path = os.path.join(self.images_dir, "test.jpg")
        category = classify_image_with_ollama(img_path, "test_model", DEFAULT_CATEGORIES)
        self.assertEqual(category, "birthdays")
        
        # Test fallback
        mock_chat.side_effect = Exception("Mock error")
        category = classify_image_with_ollama(img_path, "test_model", DEFAULT_CATEGORIES)
        self.assertEqual(category, "other")

    def test_get_image_files(self):
        # Test with valid image
        files = get_image_files(self.test_dir, min_size_threshold=700)
        self.assertEqual(len(files), 1)
        self.assertTrue(os.path.basename(files[0][1]) == "test.jpg")
        
        # Test skipping small images (mock a small one)
        small_img_path = os.path.join(self.images_dir, "small.jpg")
        Image.new("RGB", (500, 500)).save(small_img_path)
        files = get_image_files(self.test_dir, min_size_threshold=700)
        self.assertEqual(len(files), 1)  # Only the large one

if __name__ == "__main__":
    unittest.main()