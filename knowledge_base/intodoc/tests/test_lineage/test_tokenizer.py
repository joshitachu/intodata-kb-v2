"""Tests for the S4 expression tokenizer (architecture doc sec 2.2, step 1)."""

from intodoc.lineage import strip_comments_and_literals, tokenize_expression


class TestStripCommentsAndLiterals:
    def test_string_literal_collapsed_not_removed(self):
        # Collapsing to "" (not deleting) keeps the bare-reference check
        # working for expressions like `x ? row1.a : row1.b`.
        result = strip_comments_and_literals('"some literal text".equalsIgnoreCase(row1.x)')
        assert "some literal text" not in result
        assert result == '"".equalsIgnoreCase(row1.x)'

    def test_line_comment_stripped(self):
        result = strip_comments_and_literals("row1.x // trailing comment with a.dot\nrow1.y")
        assert "trailing comment" not in result
        assert "row1.x" in result and "row1.y" in result

    def test_block_comment_stripped(self):
        result = strip_comments_and_literals("row1.x /* a.b block comment */ + row1.y")
        assert "block comment" not in result

    def test_dots_inside_literal_do_not_leak(self):
        # A dot embedded in string-literal text must never be mistaken for
        # an identifier.field reference once stripped.
        result = strip_comments_and_literals('"IP=192.168.1.1".equalsIgnoreCase(row1.x)')
        assert "192.168.1.1" not in result


class TestTokenizeExpression:
    def test_bare_row_reference_resolved(self):
        tok = tokenize_expression("row1.wonum", allowed_identifiers={"row1"})
        assert tok.resolved_refs == frozenset({("row1", "wonum")})
        assert tok.dynamic_refs == frozenset()

    def test_var_always_allowed_even_if_not_declared_input_table(self):
        tok = tokenize_expression("Var.Message", allowed_identifiers={"row1"})
        assert ("Var", "Message") in tok.resolved_refs

    def test_globalmap_get_is_dynamic(self):
        tok = tokenize_expression(
            '((String)globalMap.get("key"))', allowed_identifiers={"row1"}
        )
        assert tok.resolved_refs == frozenset()
        assert "globalMap.get" in tok.dynamic_refs

    def test_context_ref_is_dynamic(self):
        tok = tokenize_expression("context.SomeVar", allowed_identifiers={"row1"})
        assert "context.SomeVar" in tok.dynamic_refs

    def test_routine_call_is_dynamic(self):
        # Long.parseLong(...) style routine/static calls are not declared
        # input tables -- flagged dynamic, never dropped.
        tok = tokenize_expression("Long.parseLong(row1.x)", allowed_identifiers={"row1"})
        assert ("row1", "x") in tok.resolved_refs
        assert "Long.parseLong" in tok.dynamic_refs

    def test_mixed_resolved_and_dynamic(self):
        tok = tokenize_expression(
            '"success".equalsIgnoreCase(row1.status) ? ((String)globalMap.get("k")) : row1.fallback',
            allowed_identifiers={"row1"},
        )
        assert ("row1", "status") in tok.resolved_refs
        assert ("row1", "fallback") in tok.resolved_refs
        assert "globalMap.get" in tok.dynamic_refs

    def test_constant_only_yields_nothing(self):
        tok = tokenize_expression('"just a literal"', allowed_identifiers={"row1"})
        assert tok.resolved_refs == frozenset()
        assert tok.dynamic_refs == frozenset()

    def test_unrelated_table_name_is_dynamic_not_resolved(self):
        # A table not in this tMap's declared input_tables must not be
        # silently trusted just because it looks like a row reference.
        tok = tokenize_expression("row99.x", allowed_identifiers={"row1"})
        assert tok.resolved_refs == frozenset()
        assert "row99.x" in tok.dynamic_refs
