# Libraries for the offline splat viewer

`lidar/view.html` loads these files from here, never from the internet, so it works on the lab laptop with no
network connection. They are unmodified copies from the npm registry:

| Folder | Package | Files | License |
|---|---|---|---|
| `three/` | `three@0.178.0` | `build/three.module.min.js`, `build/three.core.min.js`, `examples/jsm/controls/OrbitControls.js` | MIT (`three/LICENSE`) |
| `spark/` | `@sparkjsdev/spark@0.1.10` | `dist/spark.module.min.js` | MIT (`spark/LICENSE`) |

Both tarballs matched the registry's published `sha512` integrity before the files were copied out:

```
three@0.178.0             sha512-ybFIB0+x8mz0wnZgSGy2MO/WCO6xZhQSZnmfytSPyNpM0sBafGRVhdaj+erYh5U+RhQOAg/eXqw5uVDiM2BjhQ==
@sparkjsdev/spark@0.1.10  sha512-CiijdZQuj7KPDUqIZPiEqyUkJCYo1JqR05vq/V+ElxMwqR7L70ZuZDyIKcasjZHSiPB8pGRMH8HZGqUKO9aRPQ==
```

`SHA256SUMS` fingerprints each copied file. To check them (the tests in `lidar/tests/test_view.py` do it too):

```bash
cd lidar/vendor && shasum -a 256 -c SHA256SUMS
```

On Windows: `certutil -hashfile spark\spark.module.min.js SHA256`, then compare by eye.

Neither library contacts a server at run time. Spark's WebAssembly sorter is embedded in its file as a data URL,
and its only network call loads splats from a URL, which the viewer doesn't use: it reads the file you pick, or
the one `lidar/view.py` serves from this computer.

To update: `npm pack <package>@<version>`, check the tarball against `npm view <package>@<version> dist.integrity`,
copy the same files, rewrite `SHA256SUMS` and this table, and change nothing in `view.html` but the paths if
they moved.
