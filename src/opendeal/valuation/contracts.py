"""Contracts and domain models for valuation and workbook sandbox execution."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

from opendeal.core.types import Currency, FiscalPeriodType


class CalculationMode(str, Enum):
    """Execution mode for valuation recalculation."""
    HEADLESS = "HEADLESS"
    EXCEL_DESKTOP = "EXCEL_DESKTOP"


class CalculationStatus(str, Enum):
    """Recalculation status report."""
    NOT_CALCULATED_HEADLESS = "NOT_CALCULATED_HEADLESS"
    CALCULATED_DESKTOP = "CALCULATED_DESKTOP"
    CALCULATION_FAILED = "CALCULATION_FAILED"


class ValuationCellDefinition(BaseModel):
    """Specification of an explicitly authorized input cell in PEAK workbook."""
    canonical_variable: str = Field(..., description="Canonical metric or parameter identifier")
    sheet: str = Field(..., description="Target sheet name in the workbook")
    cell: str = Field(..., description="Target cell coordinate, e.g. 'O12'")
    unit: str = Field(default="", description="Financial or metric unit, e.g. USD, %, Days")
    description: str = Field(default="", description="Human-readable description of the input")
    is_formula: bool = Field(default=False, description="Must be False; formula cells cannot be inputs")

    @property
    def coordinate_key(self) -> str:
        """Returns Sheet!Cell representation."""
        return f"{self.sheet}!{self.cell}"


class ValuationInputPayload(BaseModel):
    """An input value validated and ready to be applied to a specific PEAK cell."""
    canonical_variable: str
    target_sheet: str
    target_cell: str
    value: Union[float, int, str]
    entity_id: str
    currency: Currency = Currency.USD
    fiscal_year: Optional[int] = None
    fiscal_period: Optional[FiscalPeriodType] = None
    fact_id: Optional[str] = None
    source_evidence_id: Optional[str] = None
    is_reported_zero: bool = False
    transformation_notes: Optional[str] = None

    @property
    def coordinate_key(self) -> str:
        return f"{self.target_sheet}!{self.target_cell}"


class CellWriteAudit(BaseModel):
    """Audit log entry for a cell write operation."""
    target_sheet: str
    target_cell: str
    canonical_variable: str
    previous_value: Any
    new_value: Any
    fact_id: Optional[str] = None
    applied_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "APPLIED"  # "APPLIED", "REJECTED"


class WorkbookIntegrityReport(BaseModel):
    """Integrity audit comparing master and sandboxed workbooks."""
    original_workbook_sha256_pre: str
    original_workbook_sha256_post: str
    original_workbook_unmodified: bool
    sandboxed_workbook_sha256_pre: str
    sandboxed_workbook_sha256_post: str
    formula_count_pre: int
    formula_count_post: int
    formulas_identical: bool
    formula_mismatches: List[str] = Field(default_factory=list)
    unauthorized_writes_detected: bool
    authorized_writes_count: int


class RunManifest(BaseModel):
    """Auditable machine-readable manifest of a PEAK sandbox run."""
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    entity_id: str
    original_workbook_path: str
    sandboxed_workbook_path: str
    mapping_version: str
    calculation_mode: CalculationMode
    calculation_status: CalculationStatus
    calculation_message: str
    inputs_applied: List[CellWriteAudit] = Field(default_factory=list)
    integrity_report: Optional[WorkbookIntegrityReport] = None
    extracted_outputs: Dict[str, Any] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)
