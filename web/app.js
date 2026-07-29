const textArea = document.querySelector("#shared-text");
const saveButton = document.querySelector("#save-text");
const copyTextButton = document.querySelector("#copy-text");
const clearTextButton = document.querySelector("#clear-text");
const saveState = document.querySelector("#save-state");
const fileInput = document.querySelector("#file-input");
const dropZone = document.querySelector("#drop-zone");
const fileList = document.querySelector("#file-list");
const fileCount = document.querySelector("#file-count");
const progress = document.querySelector("#upload-progress");
const progressBar = progress.querySelector("i");
const toast = document.querySelector("#toast");
const selectAll = document.querySelector("#select-all");
const downloadSelected = document.querySelector("#download-selected");
const qrCode = document.querySelector("#qr-code");
const copyAddress = document.querySelector("#copy-address");

let version = -1;
let dirty = false;
let toastTimer;

let shareAddress = `${location.protocol}//${location.host}/`;

async function loadShareAddress() {
  try {
    const response = await fetch("/api/info", { cache: "no-store" });
    const info = await response.json();
    if (info.accessUrl) shareAddress = info.accessUrl;
  } catch {
    // 服务端地址获取失败时，保留当前浏览器地址作为后备。
  }
  qrCode.src = `/api/qr?text=${encodeURIComponent(shareAddress)}`;
  copyAddress.textContent = shareAddress;
  copyAddress.title = `复制 ${shareAddress}`;
}

copyAddress.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(shareAddress);
    notify("访问地址已复制");
  } catch {
    notify("长按地址即可复制");
  }
});

loadShareAddress();

function notify(message) {
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2200);
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${(bytes / 1073741824).toFixed(1)} GB`;
}

function renderFiles(files) {
  fileCount.textContent = `${files.length} 个文件`;
  if (!files.length) {
    fileList.innerHTML = '<div class="empty">还没有文件。先放一个上来吧。</div>';
    return;
  }
  fileList.replaceChildren(...files.map(file => {
    const row = document.createElement("div");
    row.className = "file-item";
    const check = document.createElement("input");
    check.type = "checkbox";
    check.className = "file-check";
    check.value = file.name;
    check.setAttribute("aria-label", `选择 ${file.name}`);
    const link = document.createElement("a");
    link.className = "file-link";
    link.href = file.url;
    const name = document.createElement("span");
    name.className = "file-name";
    name.textContent = file.name;
    const meta = document.createElement("span");
    meta.className = "file-meta";
    meta.textContent = `${formatSize(file.size)} ↓`;
    link.append(name);
    row.append(check, link, meta);
    return row;
  }));
  selectAll.checked = false;
  selectAll.indeterminate = false;
  updateSelection();
}

function updateSelection() {
  const checks = [...document.querySelectorAll(".file-check")];
  const checked = checks.filter(check => check.checked);
  selectAll.disabled = checks.length === 0;
  selectAll.checked = checks.length > 0 && checked.length === checks.length;
  selectAll.indeterminate = checked.length > 0 && checked.length < checks.length;
  downloadSelected.disabled = checked.length === 0;
  downloadSelected.textContent = checked.length ? `下载所选（${checked.length}）` : "下载所选";
}

function downloadSelection() {
  const names = [...document.querySelectorAll(".file-check:checked")].map(check => check.value);
  if (!names.length) return;
  const form = document.createElement("form");
  form.method = "POST";
  form.action = "/api/download-zip";
  form.hidden = true;
  names.forEach(name => {
    const input = document.createElement("input");
    input.name = "files";
    input.value = name;
    form.append(input);
  });
  document.body.append(form);
  form.submit();
  form.remove();
  notify(`正在打包 ${names.length} 个文件`);
}

async function refresh() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    const state = await response.json();
    if (state.version !== version) {
      if (!dirty) textArea.value = state.text;
      renderFiles(state.files);
      version = state.version;
    }
    document.querySelector(".live span").textContent = "已连接";
    document.querySelector(".live i").style.background = "";
  } catch {
    document.querySelector(".live span").textContent = "连接中断";
    document.querySelector(".live i").style.background = "#e65c52";
  }
}

async function saveText() {
  saveButton.disabled = true;
  saveState.textContent = "保存中…";
  try {
    const response = await fetch("/api/text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: textArea.value })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    dirty = false;
    version = data.version;
    saveState.textContent = "已同步";
    notify("文字已同步给其他设备");
  } catch (error) {
    saveState.textContent = "保存失败";
    notify(error.message || "保存失败，请重试");
  } finally {
    saveButton.disabled = false;
  }
}

async function copyText() {
  try {
    await navigator.clipboard.writeText(textArea.value);
    notify("文字已复制");
  } catch {
    textArea.focus();
    textArea.select();
    const copied = document.execCommand("copy");
    textArea.setSelectionRange(textArea.value.length, textArea.value.length);
    notify(copied ? "文字已复制" : "复制失败，请手动复制");
  }
}

function clearText() {
  if (!textArea.value) {
    notify("文本框已经是空的");
    return;
  }
  textArea.value = "";
  dirty = true;
  saveState.textContent = "有未保存修改";
  textArea.focus();
  notify("已清空，保存后同步");
}

async function upload(files) {
  if (!files?.length) return;
  const form = new FormData();
  [...files].forEach(file => form.append("files", file));
  progress.hidden = false;
  progressBar.style.width = "15%";
  const request = new XMLHttpRequest();
  request.open("POST", "/api/upload");
  request.upload.onprogress = event => {
    if (event.lengthComputable) progressBar.style.width = `${event.loaded / event.total * 100}%`;
  };
  request.onload = async () => {
    progress.hidden = true;
    if (request.status >= 200 && request.status < 300) {
      const data = JSON.parse(request.responseText);
      notify(`已放入 ${data.files.length} 个文件`);
      fileInput.value = "";
      await refresh();
    } else {
      notify(JSON.parse(request.responseText).error || "上传失败");
    }
  };
  request.onerror = () => { progress.hidden = true; notify("上传失败，请检查连接"); };
  request.send(form);
}

textArea.addEventListener("input", () => { dirty = true; saveState.textContent = "有未保存修改"; });
saveButton.addEventListener("click", saveText);
copyTextButton.addEventListener("click", copyText);
clearTextButton.addEventListener("click", clearText);
document.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") saveText();
});
fileInput.addEventListener("change", () => upload(fileInput.files));
["dragenter", "dragover"].forEach(type => dropZone.addEventListener(type, event => {
  event.preventDefault(); dropZone.classList.add("dragging");
}));
["dragleave", "drop"].forEach(type => dropZone.addEventListener(type, event => {
  event.preventDefault(); dropZone.classList.remove("dragging");
}));
dropZone.addEventListener("drop", event => upload(event.dataTransfer.files));
fileList.addEventListener("change", event => {
  if (event.target.matches(".file-check")) updateSelection();
});
selectAll.addEventListener("change", () => {
  document.querySelectorAll(".file-check").forEach(check => { check.checked = selectAll.checked; });
  updateSelection();
});
downloadSelected.addEventListener("click", downloadSelection);

refresh();
