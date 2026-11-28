// Upload page: pick a video, check it in the browser, upload it with a progress bar.
(function () {
  const form = document.getElementById("video-form");
  if (!form) return;

  const allowed = JSON.parse(form.dataset.types);
  const maxBytes = parseInt(form.dataset.maxMb, 10) * 1024 * 1024;
  const maxTitle = parseInt(form.dataset.maxTitle, 10);

  const fileInput = document.getElementById("video-file");
  const titleInput = document.getElementById("video-title");
  const productSelect = document.getElementById("video-product");
  const button = document.getElementById("video-button");
  const message = document.getElementById("video-message");
  const progress = document.getElementById("video-progress");
  const bar = document.getElementById("video-bar");
  const status = document.getElementById("video-status");

  function showMessage(text) {
    message.textContent = text;
    message.hidden = !text;
  }

  fileInput.addEventListener("change", () => {
    showMessage("");
    button.disabled = true;
    const file = fileInput.files[0];
    if (!file) return;
    if (!allowed.includes(file.type)) {
      showMessage("Please choose an MP4, MOV or WebM video.");
      return;
    }
    if (file.size > maxBytes) {
      showMessage("This file is too large (max " + Math.round(maxBytes / 1048576) + " MB).");
      return;
    }
    if (!titleInput.value) {
      titleInput.value = file.name.replace(/\.[^.]+$/, "").slice(0, maxTitle);
    }
    button.disabled = false;
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = fileInput.files[0];
    if (!file) return;

    button.disabled = true;
    fileInput.disabled = true;
    showMessage("");
    progress.hidden = false;
    bar.value = 0;
    status.textContent = "Uploading... 0%";

    try {
      const result = await window.Uploader.run(file, {
        presignUrl: "/videos/presign",
        confirmUrl: "/videos/confirm",
        confirmExtra: {
          filename: file.name,
          title: titleInput.value,
          product_id: productSelect.value ? parseInt(productSelect.value, 10) : null,
        },
        onProgress: (fraction) => {
          const percent = Math.round(fraction * 100);
          bar.value = percent;
          status.textContent = "Uploading... " + percent + "%";
        },
      });
      status.textContent = "Done. Opening your video...";
      window.location.href = result.url;
    } catch (error) {
      showMessage(error.message);
      status.textContent = "";
      progress.hidden = true;
      fileInput.disabled = false;
      button.disabled = false;
    }
  });
})();
