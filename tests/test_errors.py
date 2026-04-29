"""Test VellumMem exception hierarchy."""

import pytest

from vellum.errors import VellumMemError, StoreError, VectorError, InitError


class TestExceptionHierarchy:
    def test_base_exception_is_callable(self):
        err = VellumMemError("base error")
        assert str(err) == "base error"
        assert isinstance(err, Exception)

    def test_store_error_inherits_base(self):
        err = StoreError("invalid category")
        assert str(err) == "invalid category"
        assert isinstance(err, VellumMemError)
        assert isinstance(err, Exception)

    def test_vector_error_inherits_base(self):
        err = VectorError("model not loaded")
        assert isinstance(err, VellumMemError)

    def test_init_error_inherits_base(self):
        err = InitError("download timeout")
        assert isinstance(err, VellumMemError)

    def test_catch_base_catches_all_subtypes(self):
        """A single except VellumMemError catches StoreError etc."""
        errors: list[VellumMemError] = [
            StoreError("store"),
            VectorError("vector"),
            InitError("init"),
        ]
        for err in errors:
            with pytest.raises(VellumMemError):
                raise err

    def test_has_separate_types_for_precision(self):
        """Each error type is a distinct class for fine-grained catching."""
        assert StoreError is not VectorError
        assert StoreError is not InitError
        assert VectorError is not InitError
        assert issubclass(StoreError, VellumMemError)
        assert issubclass(VectorError, VellumMemError)
        assert issubclass(InitError, VellumMemError)
