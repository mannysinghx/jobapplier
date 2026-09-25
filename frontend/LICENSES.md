# Third-party licenses — jobApplier frontend

The frontend has **two runtime dependencies** (React, React DOM). Everything else is a build-time dev dependency and is
not shipped in the bundle, except where noted. The production image is `nginx:1.27-alpine` (nginx: BSD-2-Clause) serving
static files; the `node:20-alpine` image (Node.js: MIT) is used only in the build stage.

## Direct dependencies

| Package | Version | License | Type | Shipped in `dist/`? |
|---|---|---|---|---|
| react | 18.3.1 | MIT | runtime | yes |
| react-dom | 18.3.1 | MIT | runtime | yes |
| vite | 7.3.6 | MIT | dev (bundler / dev server) | no |
| @vitejs/plugin-react | 5.2.0 | MIT | dev | no (adds no runtime code in production builds) |
| typescript | 5.6.3 | Apache-2.0 | dev (type checking) | no |
| @types/react | 18.3.x | MIT | dev (type definitions) | no |
| @types/react-dom | 18.3.x | MIT | dev (type definitions) | no |

## Transitive runtime dependencies (bundled)

| Package | Version | License |
|---|---|---|
| scheduler | 0.23.2 | MIT |
| loose-envify | 1.4.0 | MIT (build-time transform only) |
| js-tokens | 4.0.0 | MIT (build-time transform only) |

## Transitive dev dependencies

From `package-lock.json` (121 packages): 112 MIT, 5 ISC, 2 Apache-2.0, 1 BSD-3-Clause (`source-map-js`) and
1 CC-BY-4.0 (`caniuse-lite`, a browser-support data table used by the build tooling; attribution-only licence, not shipped).
All are permissive. None are copyleft.

Regenerate this summary after dependency changes:

```sh
node -e 'const l=require("./package-lock.json");for(const[k,v]of Object.entries(l.packages))if(k)console.log(k.replace(/^.*node_modules\//,""),v.version,v.license,v.dev?"dev":"runtime")'
```
