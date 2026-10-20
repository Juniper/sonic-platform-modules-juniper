
HPE (formerly Juniper) Platform Support for SONiC
=================================================

This repository hosts SONiC platform support for HPE (formerly Juniper) platforms,
including shared components and platform-specific implementations.

The public platform packages do not build, bundle, or manage product-specific
container services. Integrate those services and migrate their saved configuration
in the downstream distribution before loading it into a community image.

## Packages

- `sonic-platform-juniper-common` provides shared utilities, Python platform code, daemons, and the `juniper-common-platform-init.service` unit.
- `sonic-platform-juniper-qfx5241` provides QFX5241 kernel modules, platform utilities, device data, and the `pddf-platform-init.service` unit. It depends on the common package.

The common initialization service runs before PDDF platform initialization. The QFX5241 unit runs the QFX5241 pre-initialization hook, installs PDDF devices, and runs the post-initialization hook.

## Platform Selection

The default platform selection is `qfx5241`, set by `MODULE_DIRS` in `debian/rules`. Kernel modules are selected for the QFX5241 package from its PDDF device data and their module dependencies.

## Tests

Run the package unit tests from the repository root:

```sh
python3 -m pytest
```
