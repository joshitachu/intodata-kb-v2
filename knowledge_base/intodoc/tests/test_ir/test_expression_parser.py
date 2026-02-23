"""Unit tests for the expression parser."""

import pytest

from intodoc.ir.expression_parser import parse_expression
from intodoc.ir.models import (
    BinaryOp,
    Cast,
    ExprDataType,
    FieldRef,
    FunctionCall,
    LiteralNode,
    MethodCall,
    NullCoalesce,
    Ternary,
    UnaryOp,
    Unparsed,
)


class TestFastPath:
    """Tier 1: Regex fast path for simple expressions."""

    def test_field_ref(self):
        result = parse_expression("row1.myColumn")
        assert isinstance(result.ast, FieldRef)
        assert result.ast.table == "row1"
        assert result.ast.column == "myColumn"
        assert result.expr_type == "field_map"
        assert result.complexity_score == 1

    def test_field_ref_with_dollar(self):
        result = parse_expression("row1.$systemId")
        assert isinstance(result.ast, FieldRef)
        assert result.ast.column == "$systemId"

    def test_string_literal(self):
        result = parse_expression('"hello world"')
        assert isinstance(result.ast, LiteralNode)
        assert result.ast.value == "hello world"
        assert result.ast.data_type == ExprDataType.STRING

    def test_integer_literal(self):
        result = parse_expression("42")
        assert isinstance(result.ast, LiteralNode)
        assert result.ast.value == 42
        assert result.ast.data_type == ExprDataType.INTEGER

    def test_negative_integer(self):
        result = parse_expression("-1")
        assert isinstance(result.ast, LiteralNode)
        assert result.ast.value == -1

    def test_float_literal(self):
        result = parse_expression("3.14")
        assert isinstance(result.ast, LiteralNode)
        assert result.ast.value == 3.14
        assert result.ast.data_type == ExprDataType.DOUBLE

    def test_null_literal(self):
        result = parse_expression("null")
        assert isinstance(result.ast, LiteralNode)
        assert result.ast.value is None

    def test_true_literal(self):
        result = parse_expression("true")
        assert isinstance(result.ast, LiteralNode)
        assert result.ast.value is True
        assert result.ast.data_type == ExprDataType.BOOLEAN

    def test_false_literal(self):
        result = parse_expression("false")
        assert isinstance(result.ast, LiteralNode)
        assert result.ast.value is False

    def test_empty_string(self):
        result = parse_expression("")
        assert isinstance(result.ast, LiteralNode)
        assert result.ast.value == ""
        assert result.expr_type == "empty"


class TestTernary:
    """Ternary expressions and null coalesce detection."""

    def test_simple_ternary(self):
        result = parse_expression('row1.x != null ? row1.x : "default"')
        # Should be detected as NullCoalesce
        assert isinstance(result.ast, NullCoalesce)
        assert isinstance(result.ast.value, FieldRef)
        assert result.ast.value.column == "x"
        assert isinstance(result.ast.default, LiteralNode)
        assert result.ast.default.value == "default"

    def test_generic_ternary(self):
        result = parse_expression("row1.x > 0 ? row1.y : row1.z")
        assert isinstance(result.ast, Ternary)
        assert isinstance(result.ast.condition, BinaryOp)
        assert result.ast.condition.operator == ">"

    def test_nested_ternary(self):
        result = parse_expression('row1.a != null ? row1.a : row1.b != null ? row1.b : "none"')
        assert isinstance(result.ast, NullCoalesce)


class TestFunctionCalls:
    """Static function calls."""

    def test_static_method(self):
        result = parse_expression('StringHandling.TRIM(row1.name)')
        assert isinstance(result.ast, FunctionCall)
        assert result.ast.namespace == "StringHandling"
        assert result.ast.name == "TRIM"
        assert result.ast.canonical_name == "trim"
        assert len(result.ast.args) == 1

    def test_static_method_two_args(self):
        result = parse_expression('TalendDate.parseDate("yyyy-MM-dd", row1.dateStr)')
        assert isinstance(result.ast, FunctionCall)
        assert result.ast.canonical_name == "parse_date"
        assert len(result.ast.args) == 2

    def test_integer_parse(self):
        result = parse_expression("Integer.parseInt(row1.id)")
        assert isinstance(result.ast, FunctionCall)
        assert result.ast.canonical_name == "to_int"

    def test_string_valueof(self):
        result = parse_expression("String.valueOf(row1.num)")
        assert isinstance(result.ast, FunctionCall)
        assert result.ast.canonical_name == "to_string"


class TestMethodCalls:
    """Instance method calls."""

    def test_trim(self):
        result = parse_expression("row1.name.trim()")
        assert isinstance(result.ast, MethodCall)
        assert result.ast.method == "trim"
        assert result.ast.canonical_name == "trim"
        assert isinstance(result.ast.object, FieldRef)

    def test_chained_methods(self):
        result = parse_expression("row1.name.trim().toUpperCase()")
        assert isinstance(result.ast, MethodCall)
        assert result.ast.method == "toUpperCase"
        assert isinstance(result.ast.object, MethodCall)
        assert result.ast.object.method == "trim"

    def test_method_with_args(self):
        result = parse_expression('row1.name.substring(0, 10)')
        assert isinstance(result.ast, MethodCall)
        assert result.ast.method == "substring"
        assert len(result.ast.args) == 2

    def test_equals(self):
        result = parse_expression('row1.status.equals("ACTIVE")')
        assert isinstance(result.ast, MethodCall)
        assert result.ast.canonical_name == "equals"


class TestBinaryOps:
    """Binary operations."""

    def test_string_concat(self):
        result = parse_expression('"VBR-" + row1.id')
        assert isinstance(result.ast, BinaryOp)
        assert result.ast.operator == "+"

    def test_comparison(self):
        result = parse_expression("row1.amount > 100")
        assert isinstance(result.ast, BinaryOp)
        assert result.ast.operator == ">"

    def test_logical_and(self):
        result = parse_expression("row1.a > 0 && row1.b < 10")
        assert isinstance(result.ast, BinaryOp)
        assert result.ast.operator == "&&"

    def test_equality(self):
        result = parse_expression("row1.status == null")
        assert isinstance(result.ast, BinaryOp)
        assert result.ast.operator == "=="


class TestCasts:
    """Type cast expressions."""

    def test_int_cast(self):
        result = parse_expression("(int) row1.value")
        assert isinstance(result.ast, Cast)
        assert result.ast.target_type == "int"
        assert isinstance(result.ast.expression, FieldRef)

    def test_string_cast(self):
        result = parse_expression("(String) row1.obj")
        assert isinstance(result.ast, Cast)
        assert result.ast.target_type == "String"


class TestComplexExpressions:
    """Multi-level combinations."""

    def test_concat_with_function(self):
        result = parse_expression('"PREFIX_" + StringHandling.TRIM(row1.name)')
        assert isinstance(result.ast, BinaryOp)
        assert result.ast.operator == "+"
        assert isinstance(result.ast.right, FunctionCall)

    def test_nested_function_call(self):
        result = parse_expression('StringHandling.TRIM(StringHandling.UPCASE(row1.name))')
        assert isinstance(result.ast, FunctionCall)
        assert result.ast.canonical_name == "trim"
        assert len(result.ast.args) == 1
        inner = result.ast.args[0]
        assert isinstance(inner, FunctionCall)
        assert inner.canonical_name == "upper"


class TestErrorHandling:
    """Graceful degradation for unparseable expressions."""

    def test_never_crashes_on_garbage(self):
        result = parse_expression("@#$%^&*()")
        # Should return something, never crash
        assert result.raw == "@#$%^&*()"

    def test_unparsed_has_error(self):
        result = parse_expression("<<<invalid>>>")
        assert isinstance(result.ast, Unparsed)

    def test_complexity_scores(self):
        simple = parse_expression("row1.name")
        assert simple.complexity_score == 1

        medium = parse_expression('row1.x != null ? row1.x : "default"')
        assert medium.complexity_score >= 2

        complex_expr = parse_expression(
            'row1.a != null ? StringHandling.TRIM(row1.a) : '
            'row1.b != null ? row1.b : "fallback"'
        )
        assert complex_expr.complexity_score >= 2
