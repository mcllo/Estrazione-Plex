from __future__ import annotations

from pathlib import Path
import pandas as pd


def write_duplicate_report(output_path: Path, summary_df: pd.DataFrame, all_df: pd.DataFrame) -> None:
    keep_df = all_df[all_df["final_action"] == "KEEP"]
    conserva_df = all_df[all_df["group_status"] == "CONSERVA"]
    delete_safe_df = all_df[all_df["final_action"] == "DELETE_SAFE"]
    delete_proposed_df = all_df[all_df["final_action"] == "DELETE_PROPOSED"]
    manual_df = all_df[all_df["final_action"] == "REVIEW_MANUAL"].copy()

    manual_keys = manual_df[["group_key", "cluster_index"]].drop_duplicates() if not manual_df.empty else pd.DataFrame(columns=["group_key", "cluster_index"])
    keep_stimato_rows = pd.DataFrame()
    if not manual_keys.empty:
        merged = all_df.merge(manual_keys, on=["group_key", "cluster_index"])
        keep_stimato_rows = merged[merged["final_action"] == "KEEP"].copy()
        keep_stimato_rows["manual_role"] = "KEEP_STIMATO"
        keep_stimato_rows["reason"] = "versione tenuta con le regole attuali"
        manual_df["manual_role"] = "DA_VALUTARE"
    manual_dettaglio = pd.concat([keep_stimato_rows, manual_df], ignore_index=True) if not manual_df.empty else manual_df

    manual_index = pd.DataFrame(columns=["group_key", "title_or_episode", "manual_sheet_prefix", "keep_stimato", "num_rows_to_review", "motivo_revisione"])
    if not manual_dettaglio.empty:
        grp = manual_dettaglio.groupby(["group_key", "cluster_index"], as_index=False)
        rows = []
        for _, g in grp:
            keep = g[g.get("manual_role", "") == "KEEP_STIMATO"]["file_path"]
            rows.append({
                "group_key": g.iloc[0]["group_key"],
                "title_or_episode": g.iloc[0].get("title_or_episode", ""),
                "manual_sheet_prefix": f"MAN_{g.iloc[0]['cluster_index']}",
                "keep_stimato": keep.iloc[0] if not keep.empty else "",
                "num_rows_to_review": int((g.get("manual_role", "") == "DA_VALUTARE").sum()),
                "motivo_revisione": "restano vantaggi incrociati tra bitrate video e audio IT / sorgente",
            })
        manual_index = pd.DataFrame(rows)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="Sintesi", index=False)
        conserva_df.to_excel(writer, sheet_name="CONSERVA", index=False)
        delete_safe_df.to_excel(writer, sheet_name="ELIMINA_SICURO", index=False)
        delete_proposed_df.to_excel(writer, sheet_name="ELIMINA_PROPOSTI", index=False)
        manual_index.to_excel(writer, sheet_name="MANUALE_INDEX", index=False)
        manual_dettaglio.to_excel(writer, sheet_name="MANUALE_DETTAGLIO", index=False)
        all_df.to_excel(writer, sheet_name="TUTTE_LE_DECISIONI", index=False)
