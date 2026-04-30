from __future__ import annotations

from pathlib import Path
import pandas as pd


def write_duplicate_report(output_path: Path, summary_df: pd.DataFrame, all_df: pd.DataFrame) -> None:
    delete_df = all_df[all_df["final_action"].isin(["DELETE_SAFE", "DELETE_PROPOSED"])]
    manual_df = all_df[all_df["final_action"] == "REVIEW_MANUAL"]
    keep_df = all_df[all_df["final_action"] == "KEEP"]
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="Sintesi", index=False)
        delete_df.to_excel(writer, sheet_name="Da_eliminare", index=False)
        manual_df.to_excel(writer, sheet_name="Da_verificare", index=False)
        keep_df.to_excel(writer, sheet_name="Conserva", index=False)
        all_df.to_excel(writer, sheet_name="Tutte_le_decisioni", index=False)
