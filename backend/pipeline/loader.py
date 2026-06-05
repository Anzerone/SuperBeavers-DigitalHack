"""Load Excel file and map columns to standard format."""
from pathlib import Path

from openpyxl import load_workbook
import pandas as pd
from backend.config import COL_INDICES, EXCLUDE_INCIDENT_TYPES, EXCLUDE_OUTCOMES


def _worksheet_row_count(filepath: str) -> int | None:
    """Count source worksheet data rows, excluding the header row."""
    if Path(filepath).suffix.lower() not in {".xlsx", ".xlsm"}:
        return None

    try:
        wb = load_workbook(filepath, read_only=True, data_only=True)
    except Exception:
        return None

    try:
        return max((wb.active.max_row or 1) - 1, 0)
    finally:
        wb.close()


def load_excel(filepath: str) -> tuple[pd.DataFrame, dict]:
    """Load Excel, extract relevant columns, and apply status filters.

    Returns (filtered_df, stats) where stats has counts:
        raw_count, dropped_short_text, dropped_incident_type, dropped_outcome.
    """
    selected_indices = sorted(set(COL_INDICES.values()))
    index_to_name = {idx: name for name, idx in COL_INDICES.items()}

    df = None
    for engine in ("calamine", "openpyxl"):
        try:
            df = pd.read_excel(
                filepath,
                header=0,
                dtype=str,
                engine=engine,
                usecols=selected_indices,
            )
            rename_map = {}
            for offset, original_idx in enumerate(selected_indices):
                if offset < len(df.columns) and original_idx in index_to_name:
                    rename_map[df.columns[offset]] = index_to_name[original_idx]
            df = df.rename(columns=rename_map)
            break
        except (ValueError, ImportError):
            if engine == "openpyxl":
                df = pd.read_excel(filepath, header=0, dtype=str, engine="openpyxl")
                columns = df.columns.tolist()
                col_map = {}
                for name, idx in COL_INDICES.items():
                    if idx < len(columns):
                        col_map[columns[idx]] = name
                relevant_cols = [columns[idx] for idx in COL_INDICES.values() if idx < len(columns)]
                df = df[relevant_cols].rename(columns=col_map)

    raw_count = _worksheet_row_count(filepath) or len(df)

    # Clean text
    if "incident_text" in df.columns:
        df["incident_text"] = df["incident_text"].fillna("").astype(str).str.strip()
        df["incident_text"] = df["incident_text"].str.replace(r"<[^>]+>", " ", regex=True)
        df["incident_text"] = df["incident_text"].str.replace(r"\[club\d+\|[^\]]*\]", "", regex=True)
        df["incident_text"] = df["incident_text"].str.replace(r"\s+", " ", regex=True).str.strip()

    # Track drops separately
    before_short = len(df)
    df = df[df["incident_text"].str.len() > 10]
    dropped_short_text = before_short - len(df)

    dropped_incident_type = 0
    if "incident_type" in df.columns:
        before = len(df)
        df = df[~df["incident_type"].fillna("").isin(EXCLUDE_INCIDENT_TYPES)]
        dropped_incident_type = before - len(df)

    dropped_outcome = 0
    if "outcome" in df.columns:
        before = len(df)
        df = df[df["outcome"].isna() | ~df["outcome"].isin(EXCLUDE_OUTCOMES)]
        dropped_outcome = before - len(df)

    stats = {
        "raw_count": raw_count,
        "dropped_short_text": dropped_short_text,
        "dropped_incident_type": dropped_incident_type,
        "dropped_outcome": dropped_outcome,
    }
    return df.reset_index(drop=True), stats
