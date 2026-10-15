# Repeated Git Intelligence benchmark — October 15, 2026

Model: qwen3.5:4b Q4_K_M; CPU threads 8; context 4096; output cap 384; temperature 0.2. GPU uses 12 layers. Same scrollToRect question and indexed evidence for both modes. No model download.

| Mode | Phase | Uncached n | Mean seconds | Median | Min–max | Mean generated tokens/s | Peak sampled model VRAM |
|---|---|---:|---:|---:|---:|---:|---:|
| cpu | cold | 1 | 14.501 | 14.501 | 14.501–14.501 | 15.3 | 0 bytes |
| cpu | warm | 5 | 7.580 | 7.139 | 6.659–10.208 | 15.6 | 0 bytes |
| gpu | cold | 1 | 11.047 | 11.047 | 11.047–11.047 | 21.8 | 1,348,605,049 bytes |
| gpu | warm | 5 | 6.010 | 5.198 | 4.878–7.619 | 22.1 | 1,348,605,049 bytes |

Deterministic search: 2.46 ms median, 30 searches.
Persistent answer cache: 7.46 ms median, 10 verified cache hits.

Each mode has one unloaded cold start and five warm-model requests with application cache reads AND writes bypassed. All 12 actual inference responses contained an accepted source-linked interpretation in the original recorded run. Cached replies are measured separately, never counted as inference.
Warm requests may reuse Ollama prompt/KV state. Generated token counts vary, so token rates accompany wall-clock timings. Cold n=1 is descriptive, not statistically robust.
Sampled peak model VRAM: CPU 0 bytes; partial GPU 1,348,605,049 bytes (1.26 GiB). Device-wide usage also includes unrelated applications. Sampling may miss short transients.
RAM readings in JSON are system-wide available physical RAM, not model-only RSS. Each group includes the minimum observed available bytes. The public aggregate excludes raw runtime samples and conversations; local benchmark runs save full observations under data/benchmarks/.
The first attempt had a GPU-to-CPU fallback and was excluded. The completed run retains resource guards and allows device counters to settle between GPU requests. Settling time is outside timed inference.
For this narrow workload, observed warm GPU mean latency was lower than CPU mean latency. This is not a general performance guarantee; other applications were active and outputs differed in length.

Reviewed configuration, per-request token rates, peak memory and cache flags are in git-benchmarks.json. Runtime responses and private paths are excluded from the release. Reproduce using python benchmark_git.py --inference --runs 5.
