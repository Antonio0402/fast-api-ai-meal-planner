"""Hồi quy công thức Gemini, cache và lượng nguyên liệu; không gọi DB/mạng thật."""

import json
import unittest
from copy import deepcopy
from unittest.mock import MagicMock, patch

from app.ai.vendor.gemini_adapter import GeminiAdapter, GeminiApiError, GeminiTimeoutError
from app.api.routes.recipes import MatchInput, match_recipes
from app.core.errors import APIError
from app.schemas.app_data import Ingredient
from app.schemas.recipe import GeneratedRecipe
from app.services import recipe_generation as service
from app.services.unit_aliases import from_base

RECIPE = {
    'id': '00000000-0000-4000-8000-000000000001',
    'title': 'Rau hấp', 'minutes': 15, 'servings': 2,
    'items': [{'name': 'Rau', 'quantity': 0.3, 'unit': 'kg'}],
    'steps': ['Hấp rau theo lượng trong danh sách nguyên liệu.'],
}
INVENTORY = [Ingredient(id='rau', name='Rau', quantity=0.5, unit='kg')]


class RecipeGenerationTests(unittest.TestCase):
    def generate(self, output=None, rows=None, error=None, inventory=None):
        db = MagicMock()
        model = MagicMock()
        model.generate_text.return_value = json.dumps(output if output is not None else [RECIPE])
        model.generate_text.side_effect = error
        with patch.object(service, 'fetch_all', side_effect=rows or [[], []]), \
             patch.object(service, 'create_gemini_client', return_value=model):
            result = service.generate_recipes(db, 'user', inventory or INVENTORY, [])
        return result, db, model

    def match(self, recipe, inventory, portions=2):
        with patch('app.api.routes.recipes.generate_recipes', return_value=[recipe]):
            return match_recipes(MatchInput(inventory=inventory, portions=portions, allergies=[]),
                                 user_id='user', db=MagicMock())[0]

    def test_fractional_kg_is_sufficient_and_missing_amount_is_exact(self):
        result = self.match(RECIPE, INVENTORY)
        self.assertEqual(result['coverage'], 1)
        self.assertEqual(result['missing'], [])
        self.assertEqual(result['items'][0]['quantity'], 300)
        self.assertEqual(result['items'][0]['unit'], 'g')
        result = self.match(RECIPE, [Ingredient(id='rau', name='Rau', quantity=0.2, unit='kg')])
        self.assertEqual(result['missing'], [{'name': 'Rau', 'quantity': 100, 'unit': 'g'}])
        self.assertAlmostEqual(result['coverage'], 2 / 3)

    def test_duplicate_ingredients_share_stock_after_unit_conversion(self):
        recipe = deepcopy(RECIPE)
        recipe['items'] = [{'name': 'Rau', 'quantity': 200, 'unit': 'g'},
                           {'name': ' rau ', 'quantity': 0.2, 'unit': 'kg'}]
        result = self.match(recipe, [Ingredient(id='rau', name='Rau', quantity=200, unit='g')])
        self.assertEqual(len(result['recipe']['items']), 1)
        self.assertEqual(result['coverage'], 0.5)
        self.assertEqual(result['missing'], [{'name': 'Rau', 'quantity': 200, 'unit': 'g'}])

    def test_portions_count_units_unknown_stock_and_volume(self):
        recipe = {**RECIPE, 'items': [{'name': 'Trứng', 'quantity': 1, 'unit': 'quả'}]}
        result = self.match(recipe, [], portions=3)
        self.assertEqual(result['items'][0]['quantity'], 2)
        recipe = {**RECIPE, 'items': [{'name': 'Sữa', 'quantity': 0.25, 'unit': 'l'}]}
        for stock in [Ingredient(id='sua', name='Sữa', unit='ml'),
                      Ingredient(id='sua', name='Sữa', quantity=500, unit='g')]:
            result = self.match(recipe, [stock])
            self.assertEqual(result['missing'], [{'name': 'Sữa', 'quantity': 250, 'unit': 'ml', 'check': True}])

    def test_rounding_preserves_grams_and_positive_small_amounts(self):
        self.assertEqual(from_base(1049, 'g'), (1.049, 'kg'))
        self.assertEqual(from_base(1049.1, 'ml'), (1.05, 'l'))
        self.assertEqual(from_base(1e-12, 'g'), (1, 'g'))
        self.assertEqual(from_base(0, 'g'), (0, 'g'))

    def test_invalid_output_is_not_saved(self):
        invalid = [[], [RECIPE] * 6, [{**RECIPE, 'steps': [' ']}],
                   [{**RECIPE, 'title': ' '}], [{**RECIPE, 'items': []}],
                   [{**RECIPE, 'items': [{'name': ' ', 'quantity': 1, 'unit': 'g'}]}],
                   [{**RECIPE, 'items': [{'name': 'Rau', 'quantity': float('inf'), 'unit': 'g'}]}],
                   [{**RECIPE, 'items': [{'name': 'Muối', 'quantity': 1, 'unit': 'g'}]}],
                   [{**RECIPE, 'items': [{'name': 'Rau', 'quantity': 1, 'unit': 'ml'}]}]]
        for output in invalid:
            with self.subTest(output=output):
                db, model = MagicMock(), MagicMock()
                model.generate_text.return_value = json.dumps(output)
                with patch.object(service, 'fetch_all', return_value=[]), \
                     patch.object(service, 'create_gemini_client', return_value=model):
                    with self.assertRaises(APIError) as caught:
                        service.generate_recipes(db, 'user', INVENTORY, [])
                self.assertEqual((caught.exception.status_code, caught.exception.code), (502, 'AI_OUTPUT_INVALID'))
                db.execute.assert_not_called()

    def test_incompatible_duplicate_units_are_rejected(self):
        with self.assertRaises(ValueError):
            GeneratedRecipe.model_validate({**RECIPE, 'items': [
                {'name': 'Rau', 'quantity': 1, 'unit': 'g'},
                {'name': 'Rau', 'quantity': 1, 'unit': 'bó'},
            ]})

    def test_valid_cache_keeps_id_and_skips_gemini(self):
        result, db, model = self.generate(rows=[[{'recipe': RECIPE}]])
        self.assertEqual(result, [RECIPE])
        model.generate_text.assert_not_called()
        db.execute.assert_not_called()

    def test_invalid_cache_is_replaced_without_deleting_saved_ids(self):
        invalid = [{'recipe': {**RECIPE, 'steps': ['']}}]
        result, db, model = self.generate(rows=[invalid, invalid])
        self.assertNotEqual(result[0]['id'], RECIPE['id'])
        sql = [str(call.args[0]) for call in db.execute.call_args_list]
        self.assertTrue(any('UPDATE user_recipes SET source_hash' in query for query in sql))
        self.assertTrue(any('INSERT INTO user_recipes' in query for query in sql))
        self.assertFalse(any('DELETE' in query for query in sql))
        model.generate_text.assert_called_once()

    def test_concurrent_cache_winner_is_reused(self):
        result, db, _ = self.generate(rows=[[], [{'recipe': RECIPE}]])
        self.assertEqual(result, [RECIPE])
        self.assertFalse(any('INSERT' in str(call.args[0]) for call in db.execute.call_args_list))

    def test_schema_and_vietnamese_instructions_are_sent(self):
        _, _, model = self.generate()
        call = model.generate_text.call_args
        self.assertIn('Vietnamese', call.args[0])
        schema = call.kwargs['response_schema']
        self.assertEqual((schema['type'], schema['minItems'], schema['maxItems']), ('array', 1, 5))
        adapter = GeminiAdapter(key_router=MagicMock(), model='fixture')
        with patch.object(adapter, '_request', return_value={'candidates': [{'content': {'parts': [{'text': '[]'}]}}]}) as request:
            adapter.generate_text('test', response_schema=schema)
        self.assertEqual(request.call_args.args[0]['generationConfig']['responseJsonSchema'], schema)

    def test_all_inventory_dimensions_are_allowed(self):
        inventory = INVENTORY + [Ingredient(id='rau-2', name='Rau', quantity=1, unit='bó')]
        result, _, _ = self.generate(inventory=inventory)
        self.assertEqual(result[0]['items'], RECIPE['items'])

    def test_empty_inventory_does_not_call_provider_or_database(self):
        db = MagicMock()
        with patch.object(service, 'create_gemini_client') as model:
            self.assertEqual(service.generate_recipes(db, 'user', [], []), [])
        db.execute.assert_not_called()
        model.assert_not_called()

    def test_allergy_check_happens_before_cache(self):
        with patch.object(service, 'fetch_all', return_value=[{'normalized_name': 'rau', 'allergens': ['test']}]), \
             patch.object(service, 'create_gemini_client') as model:
            with self.assertRaises(APIError) as caught:
                service.generate_recipes(MagicMock(), 'user', INVENTORY, ['test'])
        self.assertEqual(caught.exception.code, 'NO_SAFE_INGREDIENTS')
        model.assert_not_called()

    def test_provider_codes_are_preserved(self):
        for error, code, status in [(GeminiTimeoutError('private'), 'AI_TIMEOUT', 504),
                                    (GeminiApiError(429), 'AI_RATE_LIMITED', 429),
                                    (GeminiApiError(403), 'AI_CONFIG_ERROR', 503)]:
            with self.subTest(code=code), self.assertRaises(APIError) as caught:
                self.generate(error=error)
            self.assertEqual((caught.exception.code, caught.exception.status_code), (code, status))


if __name__ == '__main__':
    unittest.main()
