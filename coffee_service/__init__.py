"""Coffee Price Prediction 로컬 분석 서비스."""

import os

# Native Torch/LightGBM 혼용은 라이브러리 초기화 전 단일 OpenMP thread로 실행한다.
os.environ.setdefault("OMP_NUM_THREADS", "1")
