"""03_8의 사용자 선택 모델을 재학습 없이 운영 artifact로 내보낸다."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil

from coffee_service.selected_models import DEFAULT_SELECTED, EXTENSIONS, SELECTION, SelectedBundle

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_RUN = ROOT / "data/processed/news_feature_ensemble/expanding_7f2729fd5ce23b69"


def export_selected(run=RESEARCH_RUN, destination=DEFAULT_SELECTED):
    run, destination = Path(run), Path(destination)
    protocol = json.loads((run / "frozen_protocol.json").read_text())
    selection = {str(h): [f"{n}@{s}" for n, s in members] for h, members in SELECTION.items()}
    if protocol["ensemble_members"] != selection or protocol["selection_cutoff"] != "2025-12-31":
        raise ValueError("Research selection differs from the approved models")
    files = {}
    for h, members in SELECTION.items():
        for name, setting in members:
            for mode in ("base", "news"):
                stem = f"{name}_{setting}_{mode}"
                for suffix in (".npz", EXTENSIONS[name]):
                    source = run / f"models/evaluation_2026_{h}" / name / (stem + suffix)
                    files[f"h{h}_{stem}{suffix}"] = source
    hashes = {name: sha256(path.read_bytes()).hexdigest() for name, path in files.items()}
    version = sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:12]
    if destination.exists():
        existing = json.loads(destination.read_text())
        if existing["version"] != version:
            raise ValueError("Use a new directory for changed model weights")
        SelectedBundle(destination)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    for name, source in files.items():
        shutil.copyfile(source, destination.parent / name)
    manifest = {"format_version": 1, "version": version, "selection": selection,
                "train_cutoff": protocol["selection_cutoff"], "features": protocol["features"],
                "news_policy": "conditional_feature", "files": hashes,
                "research_run": run.name, "versions": protocol["versions"],
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "news_live_policy": "first available session; analysis delays over 7 days excluded",
                "protocol_sha256": sha256((run / "frozen_protocol.json").read_bytes()).hexdigest()}
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    SelectedBundle(temporary)
    temporary.replace(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=RESEARCH_RUN)
    parser.add_argument("--destination", type=Path, default=DEFAULT_SELECTED)
    args = parser.parse_args()
    print(export_selected(args.run, args.destination))
