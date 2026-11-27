// Uploads the chosen images one by one:
//   1. ask the server for an upload URL   (POST .../images/presign)
//   2. PUT the file to that URL           (our server in dev, R2 in production)
//   3. tell the server it worked          (POST .../images/confirm)
(function () {
  const form = document.getElementById("upload-form");
  if (!form) return;

  const productId = form.dataset.productId;
  const angles = JSON.parse(form.dataset.angles);
  const maxImages = parseInt(form.dataset.maxImages, 10);
  const maxBytes = parseInt(form.dataset.maxMb, 10) * 1024 * 1024;
  const allowed = ["image/jpeg", "image/png", "image/webp"];

  const input = document.getElementById("file-input");
  const list = document.getElementById("upload-list");
  const button = document.getElementById("upload-button");
  const message = document.getElementById("upload-message");

  let rows = []; // { file, select, status }

  function showMessage(text) {
    message.textContent = text;
    message.hidden = !text;
  }

  input.addEventListener("change", () => {
    list.textContent = "";
    rows = [];
    showMessage("");
    let files = Array.from(input.files);
    if (files.length > maxImages) {
      showMessage("You can add " + maxImages + " more image(s) to this product. Extra files were ignored.");
      files = files.slice(0, maxImages);
    }
    for (const file of files) {
      const row = document.createElement("div");
      row.className = "upload-row";

      const name = document.createElement("span");
      name.className = "name";
      name.textContent = file.name;

      const select = document.createElement("select");
      for (const a of angles) {
        const option = document.createElement("option");
        option.value = a;
        option.textContent = a;
        select.appendChild(option);
      }

      const status = document.createElement("span");
      status.className = "status";

      if (!allowed.includes(file.type)) {
        status.textContent = "Not JPEG/PNG/WebP";
        status.classList.add("err");
      } else if (file.size > maxBytes) {
        status.textContent = "Too large";
        status.classList.add("err");
      } else {
        rows.push({ file, select, status });
      }
      row.append(name, select, status);
      list.appendChild(row);
    }
    button.disabled = rows.length === 0;
  });

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

  async function uploadOne(item) {
    const angle = item.select.value;
    const pre = await postJson("/products/" + productId + "/images/presign", {
      filename: item.file.name,
      content_type: item.file.type,
      size: item.file.size,
      angle_label: angle,
    });
    const put = await fetch(pre.upload_url, {
      method: pre.method,
      headers: pre.headers,
      body: item.file,
    });
    if (!put.ok) throw new Error("Upload failed (" + put.status + ")");
    await postJson("/products/" + productId + "/images/confirm", {
      key: pre.key,
      angle_label: angle,
    });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    button.disabled = true;
    input.disabled = true;
    showMessage("");
    let failures = 0;

    for (const item of rows) {
      if (item.done) continue;
      item.select.disabled = true;
      item.status.className = "status";
      item.status.textContent = "Uploading...";
      try {
        await uploadOne(item);
        item.done = true;
        item.status.textContent = "Done";
        item.status.classList.add("ok");
      } catch (error) {
        failures += 1;
        item.status.textContent = error.message;
        item.status.classList.add("err");
      }
    }

    if (failures === 0) {
      window.location.reload();
    } else {
      showMessage(failures + " file(s) failed. Fix the problem and press Upload to retry them.");
      button.disabled = false;
    }
  });
})();
