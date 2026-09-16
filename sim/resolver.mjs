/*
 * Two resolutions the real rules files need in order to run under plain Node.
 *
 * 1. Extensionless relative imports ("./cards"). TypeScript allows them; Node
 *    ESM does not. We patch resolution rather than edit the sources, because
 *    /src is what BoardWeaver runs — an edit here would be a rule change there.
 *
 * 2. The bare "boardweaver" specifier, mapped to the local stand-in. It lives
 *    in the repo rather than in node_modules so it is version-controlled: it is
 *    part of the harness, not a dependency.
 */
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";

const STUB = pathToFileURL(
  join(dirname(fileURLToPath(import.meta.url)), "boardweaver.js"),
).href;

export async function resolve(specifier, context, next) {
  if (specifier === "boardweaver") return { url: STUB, shortCircuit: true };
  try {
    return await next(specifier, context);
  } catch (err) {
    if (specifier.startsWith(".") && !/\.[cm]?[jt]s$/.test(specifier)) {
      return next(`${specifier}.ts`, context);
    }
    throw err;
  }
}
