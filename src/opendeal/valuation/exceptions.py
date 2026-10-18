"""Exceptions for PEAK valuation and workbook sandbox execution."""


class ValuationError(Exception):
    """Base exception for all valuation and sandbox operations."""


class UnauthorizedCellWriteError(ValuationError):
    """Raised when an operation attempts to write to an unauthorized workbook cell."""


class FormulaOverwriteProhibitedError(ValuationError):
    """Raised when an operation attempts to overwrite an Excel formula cell."""


class AmbiguousMappingError(ValuationError):
    """Raised when an input mapping is ambiguous or references a nonexistent sheet/cell."""


class ValuationInputValidationError(ValuationError):
    """Raised when financial input facts fail acceptance validation rules."""


class WorkbookIntegrityError(ValuationError):
    """Raised when workbook integrity checks detect unauthorized modifications or formula loss."""


class RecalculationUnavailableError(ValuationError):
    """Raised when formula recalculation cannot be performed in the current environment."""
