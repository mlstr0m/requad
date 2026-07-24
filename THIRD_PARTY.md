# Third-party components

The bundled engine binaries (`engine/<platform>/`) are built from
[cgg-bern/quadwild-bimdf](https://github.com/cgg-bern/quadwild-bimdf)
at commit `cbda68e5deddf9d0e24c24382852f37a6eb2a630` (GPL-3.0), with the
build patches and reproducible CI recipe published in the
[ReQuad source repository](https://github.com/mlstr0m/requad). That project
itself bundles:

| Component | License | Role |
|---|---|---|
| [QuadWild](https://github.com/nicopietroni/quadwild) | GPL-3.0 | Field computation, patch tracing, quadrangulation |
| [satsuma](https://github.com/cgg-bern/satsuma) | MIT | Bi-MDF quantization solver |
| [vcglib](https://github.com/cnr-isti-vclab/vcglib) | GPL-3.0 | Mesh processing |
| [libigl](https://github.com/libigl/libigl) | MPL-2.0 | Geometry processing |
| [OpenMesh](https://www.graphics.rwth-aachen.de/software/openmesh/) | BSD-3-Clause | Halfedge data structures |
| [CoMISo](https://www.graphics.rwth-aachen.de/software/comiso/) | GPL-3.0 | Constrained mixed-integer solver |
| [LEMON](https://lemon.cs.elte.hu/) | Boost-1.0 | Graph algorithms (flow solver) |
| [Eigen](https://eigen.tuxfamily.org/) | MPL-2.0 | Linear algebra |
| [nlohmann/json](https://github.com/nlohmann/json) | MIT | JSON parsing |
| [libTimekeeper](https://github.com/cgg-bern/libTimekeeper) | MIT | Solver timing |
| [GLEW](https://github.com/nigels-com/glew) | BSD-3-Clause / MIT | OpenGL loading |
| xfield_tracer | GPL-3.0 | Cross-field tracing |

The combined work is distributed under **GPL-3.0-or-later**.
The complete GPL text is included in `LICENSE`; the notices required by
permissive dependencies are reproduced in `THIRD_PARTY_LICENSES.md`.

## Corresponding source

The preferred source form for every bundled executable is available without
charge from the links above. The exact engine revision, ReQuad patches, build
flags, and three-platform build procedure are recorded in
`.github/workflows/build-engine.yml` in the ReQuad source repository.

## Papers

```bibtex
@article{quadwild2021,
  author  = {Pietroni, Nico and Nuvoli, Stefano and Alderighi, Thomas and
             Cignoni, Paolo and Tarini, Marco},
  title   = {Reliable Feature-Line Driven Quad-Remeshing},
  journal = {ACM Transactions on Graphics},
  volume  = {40}, number = {4}, year = {2021},
}

@article{bimdf2023,
  author  = {Heistermann, Martin and Warnett, Jethro and Bommes, David},
  title   = {Min-Deviation-Flow in Bi-directed Graphs for T-Mesh Quantization},
  journal = {ACM Transactions on Graphics},
  volume  = {42}, number = {6}, year = {2023},
}
```
