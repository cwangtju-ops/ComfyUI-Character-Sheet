# ComfyUI transport boundary

Expression Wizard uses `ComfyTransport` for the operations that cross into
ComfyUI:

1. read approved ComfyUI status and node-schema resources;
2. stage a source image under a workflow input name;
3. queue a prompt and wait for its history;
4. materialize the generated preview image in Wizard storage;
5. materialize the generated `.exp` file in Wizard storage.

`LocalComfyTransport` preserves the original single-machine behavior. It obtains
ComfyUI's input and output directories from `/system_stats`, copies the input into
the input directory, and copies both outputs back from the output directory.

`RemoteComfyTransport` uses the authenticated `comfy_gateway.py` service on the
desktop. The gateway exposes only the operations above, keeps ComfyUI bound to
loopback, restricts clients by IP address and bearer token, and validates every
filesystem path. The Wizard service, job manifests, prompt builder, parameter
validation, and UI do not know whether ComfyUI is local or remote.

## Invariants

- Expression Wizard remains the owner of job metadata and final artifacts.
- ComfyUI receives workflow input names, not Wizard filesystem paths.
- Prompt history is the source of truth for the generated image filename.
- The `.exp` filename is derived from the unique expression name in the prompt.
- Transfers must fail loudly when a source artifact is missing.
