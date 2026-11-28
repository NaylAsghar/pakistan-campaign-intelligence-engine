// Shared upload helper used by the video and thumbnail pages.
//
//   Uploader.run(file, { presignUrl, confirmUrl, confirmExtra, onProgress })
//     1. POST presignUrl   -> { key, upload_url, method, headers }
//     2. PUT the file there (our server in dev, Cloudflare R2 in production)
//        with progress events
//     3. POST confirmUrl   -> the server records the upload; its answer is returned
(function () {
  async function postJson(url, data) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(typeof body.detail === "string" ? body.detail : "Request failed (" + response.status + ")");
    }
    return body;
  }

  // fetch() cannot report upload progress, so the file goes through XMLHttpRequest.
  function putFile(url, method, headers, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open(method, url);
      for (const [name, value] of Object.entries(headers || {})) {
        xhr.setRequestHeader(name, value);
      }
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total);
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve();
        } else {
          let detail = "";
          try { detail = JSON.parse(xhr.responseText).detail || ""; } catch (e) { /* not JSON (R2) */ }
          reject(new Error(detail || "Upload failed (" + xhr.status + ")"));
        }
      };
      xhr.onerror = () => reject(new Error(
        "Upload failed. Check your connection (and the R2 CORS settings if you use R2)."));
      xhr.send(file);
    });
  }

  async function run(file, options) {
    const pre = await postJson(options.presignUrl, {
      filename: file.name,
      content_type: file.type,
      size: file.size,
    });
    await putFile(pre.upload_url, pre.method, pre.headers, file, options.onProgress);
    return postJson(options.confirmUrl, Object.assign({ key: pre.key }, options.confirmExtra || {}));
  }

  window.Uploader = { run };
})();
