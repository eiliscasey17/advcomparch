# Advanced Computer Architecture — projects

> **AI disclosure:** I used an AI assistant (Claude) to help write the code (benchmarks, run scripts, analysis and tests) and to make the plots. The writing (the report and its analysis and conclusions) is my own.

| Project | Topic |
|---|---|
| [proj1](proj1/) | SIMD vectorization study: dot product, elementwise multiply and a 3-point stencil on AMD Zen 4 (GCC 11.5 vs Clang 20) |
| [proj2](proj2/) | Memory-hierarchy characterization: L1/L2/L3/DRAM latency and bandwidth, loaded latency and Little's Law, cache- and TLB-miss impact, benchmark pitfalls (AMD Zen 4, 2 sockets) |
| [proj3](proj3/) | Storage (NVMe behind md RAID5): QD=1 latency, block-size / mix / queue-depth sweeps, throughput-latency knee and Little's Law, tail latency, LBA range, device state, benchmarking pitfalls (fio) |
