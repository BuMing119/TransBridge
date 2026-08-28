"""Story 07: MarkdownRenderer 测试 — 格式渲染与容错降级。"""

from __future__ import annotations

import unittest


class TestMarkdownRendererTokenize(unittest.TestCase):
    """MarkdownRenderer 的 tokenize 解析逻辑（不依赖 QApplication）。"""

    @classmethod
    def setUpClass(cls):
        from transbridge.infra import markdown_renderer as _mr

        cls._mr = _mr
        cls._HeadingBlock = _mr._HeadingBlock
        cls._CodeBlock = _mr._CodeBlock
        cls._ListBlock = _mr._ListBlock
        cls._TableBlock = _mr._TableBlock
        cls._HRBlock = _mr._HRBlock
        cls._ParagraphBlock = _mr._ParagraphBlock

    def _tokenize(self, text):
        return self._mr._tokenize(text)

    # ── 基本解析 ──────────────────────────────────────────────

    def test_plain_text(self):
        blocks = self._tokenize("这是一段普通文本")
        self.assertGreater(len(blocks), 0)

    def test_empty_string(self):
        blocks = self._tokenize("")
        self.assertEqual(len(blocks), 0)

    def test_whitespace_only(self):
        blocks = self._tokenize("   \n  \n  ")
        self.assertEqual(len(blocks), 0)

    def test_heading(self):
        blocks = self._tokenize("# 标题一\n## 标题二\n### 标题三")
        headings = [b for b in blocks if isinstance(b, self._HeadingBlock)]
        self.assertGreaterEqual(len(headings), 2)

    def test_code_block(self):
        text = "```python\nprint('hello')\n```"
        blocks = self._tokenize(text)
        code_blocks = [b for b in blocks if isinstance(b, self._CodeBlock)]
        self.assertGreaterEqual(len(code_blocks), 1)

    def test_unordered_list(self):
        text = "- 项目一\n- 项目二\n- 项目三"
        blocks = self._tokenize(text)
        lists = [b for b in blocks if isinstance(b, self._ListBlock)]
        self.assertGreaterEqual(len(lists), 1)

    def test_ordered_list(self):
        text = "1. 第一\n2. 第二\n3. 第三"
        blocks = self._tokenize(text)
        lists = [b for b in blocks if isinstance(b, self._ListBlock)]
        self.assertGreaterEqual(len(lists), 1)

    def test_table(self):
        text = "| 列A | 列B |\n|-----|-----|\n| a1  | b1  |"
        blocks = self._tokenize(text)
        tables = [b for b in blocks if isinstance(b, self._TableBlock)]
        self.assertEqual(len(tables), 1)

    def test_horizontal_rule(self):
        text = "段落一\n---\n段落二"
        blocks = self._tokenize(text)
        hrs = [b for b in blocks if isinstance(b, self._HRBlock)]
        self.assertEqual(len(hrs), 1)

    def test_bold_and_italic(self):
        text = "这是 **粗体** 和 *斜体* 文本"
        blocks = self._tokenize(text)
        self.assertGreater(len(blocks), 0)

    # ── 容错 ──────────────────────────────────────────────────

    def test_unclosed_tags(self):
        """未闭合标签应降级为纯文本，不抛异常。"""
        text = "**粗体开始但没有结束"
        try:
            blocks = self._tokenize(text)
            self.assertGreaterEqual(len(blocks), 0)
        except Exception as exc:
            self.fail(f"未闭合标签导致异常: {exc}")

    def test_mixed_format(self):
        """混搭格式应正确解析。"""
        text = "# 标题\n**粗体**和*斜体*混用\n- 列表项\n```\ncode\n```"
        try:
            blocks = self._tokenize(text)
            self.assertGreater(len(blocks), 2)
        except Exception as exc:
            self.fail(f"混搭格式解析异常: {exc}")

    def test_malformed_table(self):
        """不规范的表格应降级处理。"""
        text = "| 只有一行 |\n没有分隔行"
        try:
            blocks = self._tokenize(text)
            self.assertGreaterEqual(len(blocks), 0)
        except Exception as exc:
            self.fail(f"不规范表格导致异常: {exc}")


class TestMarkdownRendererRender(unittest.TestCase):
    """render() 方法测试（需要 QApplication）。"""

    def test_render_returns_widget(self):
        from PyQt6.QtWidgets import QApplication, QWidget

        app = QApplication.instance()
        if app is None:
            raise unittest.SkipTest("无 QApplication 实例，跳过渲染测试")
        from transbridge.infra.markdown_renderer import MarkdownRenderer

        r = MarkdownRenderer()
        widget = r.render("## 测试标题\n测试内容")
        self.assertIsInstance(widget, QWidget)

    def test_render_fallback_on_junk(self):
        from PyQt6.QtWidgets import QApplication, QWidget

        app = QApplication.instance()
        if app is None:
            raise unittest.SkipTest("无 QApplication 实例，跳过渲染测试")
        from transbridge.infra.markdown_renderer import MarkdownRenderer

        r = MarkdownRenderer()
        widget = r.render("\x00\x01\x02")
        self.assertIsInstance(widget, QWidget)


if __name__ == "__main__":
    unittest.main()
