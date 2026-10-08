using Workerd = import "./node_modules/workerd/workerd.capnp";
const config :Workerd.Config = (
  services = [
    (name = "main", worker = (
      modules = [
        (name = "tests.mjs", esModule = embed "tests.mjs"),
        (name = "relay.mjs", esModule = embed "../relay.mjs")
      ],
      compatibilityDate = "2026-09-18",
      bindings = [(name = "WIRE", service = (name = "main", entrypoint = "wire"))]
    )),
    # Native fixtures only. Even an accidental global fetch has no network route.
    (name = "internet", network = (allow = []))
  ]
);
