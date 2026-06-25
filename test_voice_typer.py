import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

# Since we want to test the voice_typer module, we'll import it.
# We'll mock tk, sounddevice, and other system dependencies that are not needed for localization tests.
with patch('sounddevice.InputStream'):
    with patch('pynput.keyboard.Listener'):
        import voice_typer
        from voice_typer import LOCALIZATION, VoiceTyperApp

class TestVoiceTyperLocalization(unittest.TestCase):
    def test_localization_keys_match(self):
        """Verify that English and Russian localization dicts have the exact same keys."""
        ru_keys = set(LOCALIZATION["ru"].keys())
        en_keys = set(LOCALIZATION["en"].keys())
        self.assertEqual(ru_keys, en_keys, "Localization dictionaries must have identical keys")

    @patch('voice_typer.tk.Tk')
    @patch('voice_typer.pystray.Icon')
    @patch('voice_typer.whisper.load_model')
    @patch('voice_typer.keyboard.Listener')
    def test_get_text_helper(self, mock_listener, mock_whisper, mock_icon, mock_tk):
        """Test the _get_text method under different language settings."""
        # Provide minimal config to avoid KeyError
        config = {"language": "ru", "app_language": "ru", "model_size": "base", "device": "auto"}
        with patch.object(VoiceTyperApp, '_load_config', return_value=config):
            with patch.object(VoiceTyperApp, '_save_config_to_file'):
                app = VoiceTyperApp(Path("dummy_path"))
                
                # Test RU translation
                app.config["app_language"] = "ru"
                self.assertEqual(app._get_text("ready_to_work"), "ГОТОВ К РАБОТЕ")
                
                # Test formatting RU
                self.assertEqual(app._get_text("language_status", lang="RU"), "ЯЗЫК: RU")
                
                # Switch to EN and test translation
                app.config["app_language"] = "en"
                self.assertEqual(app._get_text("ready_to_work"), "READY TO WORK")
                
                # Test formatting EN
                self.assertEqual(app._get_text("language_status", lang="EN"), "LANG: EN")
                
                # Test fallback for invalid language
                app.config["app_language"] = "fr"
                self.assertEqual(app._get_text("ready_to_work"), "ГОТОВ К РАБОТЕ")



if __name__ == '__main__':
    unittest.main()
