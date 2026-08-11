# OCR 配置与变更记录

- 更新时间：2026-08-11
- 当前配置来源：`ocr_profiles` 中唯一的 active profile

OCR 只用于消息处理的条件阶段 `image_ocr` 和独立的 OCR Lab。原始图片由
`media_assets.storage_path` 指向，OCR 文本、结构化提取和测试结果都是可重建的派生数据，
不得写回 `raw_items.content_blocks`。

## 当前默认 Profile

当前 fresh database 会创建 `production-2026-07-25`，参数如下：

```json
{
  "scale": 2,
  "grayscale": false,
  "contrast": 1,
  "sharpness": 1,
  "text_score": null,
  "box_thresh": null,
  "unclip_ratio": 1.2,
  "use_cls": true,
  "divider_x_ratio": null,
  "line_brightness": 105,
  "line_coverage": 0.82
}
```

默认值定义在 `services/api/scripts/migrate_database.py::DEFAULT_OCR_PROFILE`。运行时从
`ocr_profiles` 选择 `is_active=true` 且 `updated_at` 最新的记录；没有 active profile 时使用
RapidOCR 默认参数。

## 变更流程

1. 在管理台 OCR Lab 对真实原始媒体创建 `ocr_test_runs`，不得直接修改 active profile。
2. 核对原始 OCR 行、表格结构、overlay 和置信度。
3. 通过激活测试结果创建新的 `ocr_profiles` 记录；激活时原 active profile 自动失效。
4. 用相关 pipeline 测试验证 `image_ocr -> translation -> message_analysis` 后再投入自动处理。
5. 参数或确定性表格规则改变时，同时更新测试和本文档；不要改写旧 profile。

## 数据保留边界

- 保留：`ocr_profiles`、RawItem、原始 `media_assets` 记录和原始图片文件。
- 可重建：`ocr_test_runs`、`media_extractions`、overlay、`media_assets.ocr_text` 和发布副本。
- 下游重置不得删除 active profile，也不得删除或改写原始图片。
