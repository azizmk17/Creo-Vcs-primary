import sqlite3

DB = r"d:\mineDocs\WORK-SPACE\Python-scripts\creo-vcs\creo_vcs_v4-plm_v1.1\creo_vcs.db"

def cols(cur, t: str):
    return [r[1] for r in cur.execute(f"PRAGMA table_info({t})").fetchall()]

if __name__ == "__main__":
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    for t in ("bom", "part_files", "part_file_versions", "commits"):
        try:
            print(f"{t}={','.join(cols(cur, t))}")
        except Exception as e:
            print(f"{t}=ERROR:{e}")
