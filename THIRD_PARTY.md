# Third-party components

The bundled engine binaries (`engine/<platform>/`) are built from
[cgg-bern/quadwild-bimdf](https://github.com/cgg-bern/quadwild-bimdf)
(GPL-3.0), with the build patches in `patches/`. That project itself
bundles:

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
| xfield_tracer | GPL-3.0 | Cross-field tracing |

The combined work is distributed under **GPL-3.0-or-later**.

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
