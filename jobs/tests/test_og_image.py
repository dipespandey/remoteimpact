from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase
from PIL import Image, ImageDraw
from jobs.services.social_card import fit_text, load_font, render_job_card


class SocialCardTests(SimpleTestCase):
    def test_title_fits(self):
        draw = ImageDraw.Draw(Image.new('RGB', (1200, 630)))
        title = 'Managing Director, Entertainment'
        font, lines, size = fit_text(draw, title, 1060, 3, range(88, 53, -2))
        self.assertEqual(' '.join(lines), title)
        self.assertGreaterEqual(size, 54)
        self.assertTrue(all(draw.textlength(line, font=font) <= 1060 for line in lines))

    def test_extreme_titles_are_bounded(self):
        draw = ImageDraw.Draw(Image.new('RGB', (1200, 630)))
        for title in ['Long role name ' * 50, 'W' * 300, '']:
            font, lines, _ = fit_text(draw, title, 1060, 3, range(88, 53, -2))
            self.assertLessEqual(len(lines), 3)
            self.assertTrue(all(draw.textlength(line, font=font) <= 1060 for line in lines))

    def test_fallback_respects_size(self):
        with patch('jobs.services.social_card.ImageFont.truetype', side_effect=OSError):
            with patch('jobs.services.social_card.ImageFont.load_default') as fallback:
                load_font(80)
                fallback.assert_called_once_with(size=80)

    def test_image_dimensions(self):
        card = render_job_card(SimpleNamespace(title='Programme Director', organization=None))
        self.assertEqual(card.size, (1200, 630))
        self.assertEqual(card.mode, 'RGB')
