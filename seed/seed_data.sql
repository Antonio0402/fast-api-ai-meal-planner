-- Data copied from FreshPlan/src/services/api.ts on 2026-10-11.
-- Ingredient groups and recipe contents intentionally match the mobile MVP seed.
BEGIN;

INSERT INTO ingredients (name, normalized_name, shopping_group)
VALUES
    ('Trứng', 'trung', 'Trứng & sữa'),
    ('Sữa', 'sua', 'Trứng & sữa'),
    ('Cà chua', 'ca chua', 'Rau củ'),
    ('Hành lá', 'hanh la', 'Rau củ'),
    ('Dầu ăn', 'dau an', 'Gia vị & đồ khô'),
    ('Gạo', 'gao', 'Gia vị & đồ khô'),
    ('Bột mì', 'bot mi', 'Gia vị & đồ khô'),
    ('Đậu phụ', 'dau phu', 'Đạm'),
    ('Tôm', 'tom', 'Đạm'),
    ('Thịt bò', 'thit bo', 'Đạm')
ON CONFLICT (normalized_name) DO UPDATE
SET name = EXCLUDED.name,
    shopping_group = EXCLUDED.shopping_group,
    updated_at = now();

INSERT INTO recipes (id, title, minutes, base_servings, is_active)
VALUES
    ('r1', 'Trứng sốt cà chua', 20, 2, true),
    ('r2', 'Canh cà chua trứng', 20, 3, true),
    ('r3', 'Bánh kếp sữa trứng', 25, 2, true),
    ('r4', 'Cơm chiên trứng', 15, 2, true),
    ('r5', 'Đậu phụ sốt cà chua', 25, 2, true),
    ('r6', 'Tôm rang hành', 20, 2, true),
    ('r7', 'Bò xào cà chua', 20, 2, true),
    ('r8', 'Cơm chiên thịt bò', 20, 2, true),
    ('r9', 'Canh bò cà chua', 30, 3, true)
ON CONFLICT (id) DO UPDATE
SET title = EXCLUDED.title,
    minutes = EXCLUDED.minutes,
    base_servings = EXCLUDED.base_servings,
    is_active = EXCLUDED.is_active,
    updated_at = now();

INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity, unit_code, position)
SELECT seed.recipe_id, ingredients.id, seed.quantity, seed.unit_code, seed.position
FROM (VALUES
    ('r1', 'trung', 3::numeric, 'quả', 0),
    ('r1', 'ca chua', 200::numeric, 'g', 1),
    ('r1', 'hanh la', 20::numeric, 'g', 2),
    ('r1', 'dau an', 15::numeric, 'ml', 3),
    ('r2', 'trung', 2::numeric, 'quả', 0),
    ('r2', 'ca chua', 250::numeric, 'g', 1),
    ('r2', 'hanh la', 10::numeric, 'g', 2),
    ('r3', 'trung', 2::numeric, 'quả', 0),
    ('r3', 'sua', 200::numeric, 'ml', 1),
    ('r3', 'bot mi', 150::numeric, 'g', 2),
    ('r4', 'gao', 200::numeric, 'g', 0),
    ('r4', 'trung', 2::numeric, 'quả', 1),
    ('r4', 'hanh la', 10::numeric, 'g', 2),
    ('r4', 'dau an', 10::numeric, 'ml', 3),
    ('r5', 'dau phu', 300::numeric, 'g', 0),
    ('r5', 'ca chua', 200::numeric, 'g', 1),
    ('r5', 'hanh la', 10::numeric, 'g', 2),
    ('r5', 'dau an', 15::numeric, 'ml', 3),
    ('r6', 'tom', 250::numeric, 'g', 0),
    ('r6', 'hanh la', 20::numeric, 'g', 1),
    ('r6', 'dau an', 10::numeric, 'ml', 2),
    ('r7', 'thit bo', 250::numeric, 'g', 0),
    ('r7', 'ca chua', 200::numeric, 'g', 1),
    ('r7', 'hanh la', 10::numeric, 'g', 2),
    ('r7', 'dau an', 15::numeric, 'ml', 3),
    ('r8', 'gao', 200::numeric, 'g', 0),
    ('r8', 'thit bo', 150::numeric, 'g', 1),
    ('r8', 'trung', 1::numeric, 'quả', 2),
    ('r8', 'hanh la', 10::numeric, 'g', 3),
    ('r8', 'dau an', 15::numeric, 'ml', 4),
    ('r9', 'thit bo', 200::numeric, 'g', 0),
    ('r9', 'ca chua', 250::numeric, 'g', 1),
    ('r9', 'hanh la', 10::numeric, 'g', 2)
) AS seed(recipe_id, normalized_name, quantity, unit_code, position)
JOIN ingredients ON ingredients.normalized_name = seed.normalized_name
ON CONFLICT (recipe_id, position) DO UPDATE
SET ingredient_id = EXCLUDED.ingredient_id,
    quantity = EXCLUDED.quantity,
    unit_code = EXCLUDED.unit_code;

INSERT INTO recipe_steps (recipe_id, position, text)
VALUES
    ('r1', 0, 'Đánh trứng với chút muối.'),
    ('r1', 1, 'Xào cà chua đến mềm.'),
    ('r1', 2, 'Cho trứng vào đảo đều, rắc hành lá.'),
    ('r2', 0, 'Phi thơm hành, xào cà chua.'),
    ('r2', 1, 'Thêm nước, đun sôi.'),
    ('r2', 2, 'Đánh trứng, rót vòng tròn vào nồi.'),
    ('r3', 0, 'Trộn bột, trứng, sữa thành hỗn hợp mịn.'),
    ('r3', 1, 'Chiên từng chiếc trên chảo chống dính.'),
    ('r4', 0, 'Nấu cơm và để nguội.'),
    ('r4', 1, 'Phi hành, đập trứng vào đảo.'),
    ('r4', 2, 'Cho cơm vào chiên đều tay.'),
    ('r5', 0, 'Chiên vàng đậu phụ.'),
    ('r5', 1, 'Xào cà chua thành sốt.'),
    ('r5', 2, 'Cho đậu vào đun nhỏ lửa 5 phút.'),
    ('r6', 0, 'Ướp tôm với chút muối.'),
    ('r6', 1, 'Rang tôm đến khi đổi màu.'),
    ('r6', 2, 'Cho hành lá vào đảo nhanh.'),
    ('r7', 0, 'Thái bò mỏng, ướp chút muối tiêu.'),
    ('r7', 1, 'Xào nhanh bò lửa lớn rồi múc ra.'),
    ('r7', 2, 'Xào cà chua, cho bò vào đảo đều, rắc hành lá.'),
    ('r8', 0, 'Nấu cơm và để nguội.'),
    ('r8', 1, 'Xào bò thái hạt lựu, đập trứng vào đảo.'),
    ('r8', 2, 'Cho cơm vào chiên đều, rắc hành lá.'),
    ('r9', 0, 'Phi thơm hành, xào bò sơ.'),
    ('r9', 1, 'Thêm cà chua và nước, đun sôi.'),
    ('r9', 2, 'Hầm nhỏ lửa 15 phút, rắc hành lá.')
ON CONFLICT (recipe_id, position) DO UPDATE
SET text = EXCLUDED.text;

COMMIT;
