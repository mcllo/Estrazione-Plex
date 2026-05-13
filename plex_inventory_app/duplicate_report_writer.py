from __future__ import annotations

from pathlib import Path
import pandas as pd


def write_duplicate_report(output_path: Path, summary_df: pd.DataFrame, all_df: pd.DataFrame) -> None:
    keep_df = all_df[all_df["final_action"] == "KEEP"]
    delete_safe_df = all_df[all_df["final_action"] == "DELETE_SAFE"]
    delete_proposed_df = all_df[all_df["final_action"] == "DELETE_PROPOSED"]
    manual_df = all_df[all_df["final_action"] == "REVIEW_MANUAL"]
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="Sintesi", index=False)
        keep_df.to_excel(writer, sheet_name="CONSERVA", index=False)
        delete_safe_df.to_excel(writer, sheet_name="ELIMINA_SICURO", index=False)
        delete_proposed_df.to_excel(writer, sheet_name="ELIMINA_PROPOSTI", index=False)
        manual_df[["group_key", "cluster_index", "file_path", "reason"]].to_excel(writer, sheet_name="MANUALE_INDEX", index=False)
        manual_df.to_excel(writer, sheet_name="MANUALE_DETTAGLIO", index=False)
        all_df.to_excel(writer, sheet_name="TUTTE_LE_DECISIONI", index=False)
