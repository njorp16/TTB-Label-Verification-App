const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const MAX_TEXT_LENGTHS = {
  brand_name: 200,
  product_class: 200,
  producer: 300,
  country: 100,
  abv: 50,
  net_contents: 50,
  government_warning: 4000,
};
const UPLOAD_IMAGE_MAX_SIDE = 1024;
const UPLOAD_JPEG_QUALITY = 0.8;
const ACCEPTED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

const FIELD_LABELS = {
  brand_name: "Brand Name",
  product_class: "Type of Product",
  producer: "Producer or Bottler Name",
  country: "Country of Origin",
  abv: "Alcohol Percentage (ABV)",
  net_contents: "Container Size (Net Contents)",
  government_warning: "Government Health Warning",
};

const formView = document.getElementById("form-view");
const resultsView = document.getElementById("results-view");
const form = document.getElementById("verification-form");
const fieldset = document.getElementById("verification-fields");
const imageInput = document.getElementById("image");
const imagePreview = document.getElementById("image-preview");
const selectedFile = document.getElementById("selected-file");
const errorSummary = document.getElementById("error-summary");
const loadingStatus = document.getElementById("loading-status");
const submitButton = document.getElementById("submit-button");
const buttonLabel = submitButton.querySelector(".button-label");
const spinner = submitButton.querySelector(".spinner");
const resetButton = document.getElementById("reset-button");
const singleModeButton = document.getElementById("single-mode-button");
const batchModeButton = document.getElementById("batch-mode-button");
const batchForm = document.getElementById("batch-form");
const batchFieldset = document.getElementById("batch-fields");
const batchItems = document.getElementById("batch-items");
const batchItemTemplate = document.getElementById("batch-item-template");
const addBatchItemButton = document.getElementById("add-batch-item");
const batchProgress = document.getElementById("batch-progress");
const batchProgressText = document.getElementById("batch-progress-text");
const batchResultsView = document.getElementById("batch-results-view");
const batchResetButton = document.getElementById("batch-reset-button");

let previewUrl = null;
let nextBatchItemId = 1;
let batchProgressTimer = null;

class DisplayError extends Error {}

imageInput.addEventListener("change", () => {
  clearFieldError("image");
  updateImagePreview();
});

form.addEventListener("input", (event) => {
  if (Object.hasOwn(FIELD_LABELS, event.target.name)) {
    clearFieldError(event.target.name);
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearErrors();

  const firstInvalidControl = validateForm();
  if (firstInvalidControl) {
    showErrorSummary("Please correct the highlighted information and try again.");
    firstInvalidControl.focus();
    return;
  }

  setLoading(true);

  try {
    const body = new FormData(form);
    const optimizedImage = await optimizeImageForUpload(imageInput.files[0]);
    body.set("image", optimizedImage, optimizedFilename(imageInput.files[0].name));
    const response = await fetch("/verify", {
      method: "POST",
      body,
    });

    const payload = await readJson(response);
    if (!response.ok) {
      const message = typeof payload?.message === "string"
        ? payload.message
        : "We could not check this label. Please try again.";
      throw new DisplayError(message);
    }

    if (!isVerificationResult(payload)) {
      throw new Error("Unexpected verification response");
    }

    renderResults(payload);
  } catch (error) {
    const message = error instanceof DisplayError
      ? error.message
      : "We could not check this label. Please check your connection and try again.";
    showErrorSummary(message);
    errorSummary.focus();
  } finally {
    setLoading(false);
  }
});

resetButton.addEventListener("click", resetPage);
singleModeButton.addEventListener("click", () => setMode("single"));
batchModeButton.addEventListener("click", () => setMode("batch"));
addBatchItemButton.addEventListener("click", () => addBatchItem(true));
batchResetButton.addEventListener("click", resetBatchPage);

batchForm.addEventListener("input", (event) => {
  if (event.target.matches("[data-name]")) {
    clearBatchFieldError(event.target);
  }
});

batchForm.addEventListener("change", (event) => {
  if (event.target.matches('[data-name="image"]')) {
    clearBatchFieldError(event.target);
    const card = event.target.closest(".batch-card");
    const selected = card.querySelector("[data-selected-file]");
    selected.textContent = event.target.files[0]
      ? `Selected photo: ${event.target.files[0].name}`
      : "No photo selected.";
  }
});

batchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearBatchErrors();
  const cards = [...batchItems.querySelectorAll(".batch-card")];
  const firstInvalid = validateBatch(cards);
  if (firstInvalid) {
    showErrorSummary("Please correct the highlighted information and try again.");
    firstInvalid.focus();
    return;
  }

  const body = new FormData();
  const applications = cards.map((card) => {
    const application = {};
    Object.keys(FIELD_LABELS).forEach((name) => {
      application[name] = card.querySelector(`[data-name="${name}"]`).value;
    });
    return application;
  });
  body.append("applications", JSON.stringify(applications));
  setBatchLoading(true, cards.length);

  try {
    const optimizedImages = await Promise.all(cards.map(async (card) => {
      const file = card.querySelector('[data-name="image"]').files[0];
      return [await optimizeImageForUpload(file), optimizedFilename(file.name)];
    }));
    optimizedImages.forEach(([file, name]) => body.append("images", file, name));
    const response = await fetch("/verify/batch", { method: "POST", body });
    const payload = await readJson(response);
    if (!response.ok) {
      throw new DisplayError(
        typeof payload?.message === "string"
          ? payload.message
          : "We could not check this batch. Please try again.",
      );
    }
    if (!isBatchResult(payload)) {
      throw new Error("Unexpected batch response");
    }
    renderBatchResults(payload);
  } catch (error) {
    const message = error instanceof DisplayError
      ? error.message
      : "We could not check this batch. Please check your connection and try again.";
    showErrorSummary(message);
    errorSummary.focus();
  } finally {
    setBatchLoading(false, cards.length);
  }
});

addBatchItem();
addBatchItem();

function validateForm() {
  let firstInvalidControl = null;
  const file = imageInput.files[0];

  if (!file) {
    firstInvalidControl = markInvalid(imageInput, "Choose a label photo.", firstInvalidControl);
  } else if (!ACCEPTED_IMAGE_TYPES.has(file.type)) {
    firstInvalidControl = markInvalid(
      imageInput,
      "Choose a JPEG, PNG, or WEBP photo.",
      firstInvalidControl,
    );
  } else if (file.size > MAX_UPLOAD_BYTES) {
    firstInvalidControl = markInvalid(
      imageInput,
      "Choose a photo smaller than 10 MB.",
      firstInvalidControl,
    );
  }

  Object.entries(FIELD_LABELS).forEach(([name, label]) => {
    const control = form.elements.namedItem(name);
    if (!control.value.trim()) {
      firstInvalidControl = markInvalid(
        control,
        `Enter the ${label}.`,
        firstInvalidControl,
      );
    } else if (control.value.length > MAX_TEXT_LENGTHS[name]) {
      firstInvalidControl = markInvalid(control, `${label} is too long.`, firstInvalidControl);
    }
  });

  firstInvalidControl = validateApplicationFormats(form, firstInvalidControl);

  return firstInvalidControl;
}

function markInvalid(control, message, firstInvalidControl) {
  control.setAttribute("aria-invalid", "true");
  document.getElementById(`${control.id}-error`).textContent = message;
  return firstInvalidControl || control;
}

function clearFieldError(name) {
  const control = form.elements.namedItem(name);
  control.removeAttribute("aria-invalid");
  document.getElementById(`${name}-error`).textContent = "";
}

function clearErrors() {
  errorSummary.hidden = true;
  errorSummary.textContent = "";
  ["image", ...Object.keys(FIELD_LABELS)].forEach(clearFieldError);
}

function showErrorSummary(message) {
  errorSummary.textContent = message;
  errorSummary.hidden = false;
}

function updateImagePreview() {
  releasePreviewUrl();
  const file = imageInput.files[0];

  if (!file) {
    selectedFile.textContent = "No photo selected.";
    imagePreview.hidden = true;
    imagePreview.removeAttribute("src");
    return;
  }

  selectedFile.textContent = `Selected photo: ${file.name}`;
  if (!ACCEPTED_IMAGE_TYPES.has(file.type)) {
    imagePreview.hidden = true;
    imagePreview.removeAttribute("src");
    return;
  }

  previewUrl = URL.createObjectURL(file);
  imagePreview.src = previewUrl;
  imagePreview.hidden = false;
}

function validateApplicationFormats(container, firstInvalidControl) {
  const abv = container.querySelector('[name="abv"], [data-name="abv"]');
  const netContents = container.querySelector('[name="net_contents"], [data-name="net_contents"]');
  const abvMatch = abv.value.match(/\d+(?:\.\d+)?/);
  if (abv.value.trim() && (!abvMatch || Number(abvMatch[0]) <= 0 || Number(abvMatch[0]) > 100)) {
    firstInvalidControl = container === form
      ? markInvalid(abv, "Enter an alcohol percentage between 0 and 100, such as 13.5%.", firstInvalidControl)
      : markBatchInvalid(abv, "Enter an alcohol percentage between 0 and 100, such as 13.5%.", firstInvalidControl);
  }
  const netMatch = netContents.value.match(/^\s*(\d+(?:\.\d+)?)\s*(?:ml|milliliters?|millilitres?|l|liters?|litres?)\s*$/i);
  if (netContents.value.trim() && (!netMatch || Number(netMatch[1]) <= 0)) {
    firstInvalidControl = container === form
      ? markInvalid(netContents, "Enter a positive container size in mL or L, such as 750 mL.", firstInvalidControl)
      : markBatchInvalid(netContents, "Enter a positive container size in mL or L, such as 750 mL.", firstInvalidControl);
  }
  return firstInvalidControl;
}

async function optimizeImageForUpload(file) {
  if (!("createImageBitmap" in window)) return file;
  let bitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, UPLOAD_IMAGE_MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    if (scale === 1 && file.size <= 1024 * 1024) return file;
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", UPLOAD_JPEG_QUALITY));
    return blob || file;
  } catch {
    return file;
  } finally {
    bitmap?.close();
  }
}

function optimizedFilename(filename) {
  return filename.replace(/\.[^.]+$/, "") + ".jpg";
}

function releasePreviewUrl() {
  if (previewUrl) {
    URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  }
}

function setLoading(isLoading) {
  fieldset.disabled = isLoading;
  buttonLabel.textContent = isLoading ? "Checking Label…" : "Check This Label";
  spinner.hidden = !isLoading;
  loadingStatus.textContent = isLoading ? "Checking the label. Please wait." : "";
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function isVerificationResult(payload) {
  return payload
    && (payload.verdict === "PASS" || payload.verdict === "NEEDS_REVIEW")
    && Array.isArray(payload.fields)
    && payload.fields.every((field) => (
      typeof field.field_name === "string"
      && (field.status === "PASS" || field.status === "FAIL")
      && typeof field.expected === "string"
      && typeof field.actual === "string"
    ));
}

function renderResults(result) {
  const failedFields = result.fields.filter((field) => field.status === "FAIL");
  const passedFields = result.fields.filter((field) => field.status === "PASS");
  const approved = result.verdict === "PASS";

  const banner = document.getElementById("verdict-banner");
  const verdict = document.getElementById("result-verdict");
  const summary = document.getElementById("result-summary");
  const failedSection = document.getElementById("failed-section");
  const failedResults = document.getElementById("failed-results");
  const passedSection = document.getElementById("passed-section");
  const passedResults = document.getElementById("passed-results");

  banner.className = `verdict-banner ${approved ? "verdict-approved" : "verdict-review"}`;
  verdict.textContent = approved ? "APPROVED" : "NEEDS REVIEW";
  summary.textContent = approved
    ? "All 7 items match the label."
    : `${failedFields.length} ${failedFields.length === 1 ? "item needs" : "items need"} attention.`;

  failedResults.replaceChildren(...failedFields.map(createFailureCard));
  passedResults.replaceChildren(...passedFields.map(createPassRow));
  failedSection.hidden = failedFields.length === 0;
  passedSection.hidden = passedFields.length === 0;

  formView.hidden = true;
  resultsView.hidden = false;
  window.scrollTo({ top: 0, behavior: "auto" });
  verdict.focus();
}

function createFailureCard(field) {
  const card = document.createElement("article");
  card.className = "result-card result-fail";

  const header = document.createElement("div");
  header.className = "result-card-header";
  const title = document.createElement("h3");
  title.textContent = displayFieldName(field.field_name);
  header.append(title, createStatusBadge("FAIL"));

  const explanation = document.createElement("p");
  explanation.className = "failure-explanation";
  explanation.textContent = failureExplanation(field);

  const comparison = document.createElement("dl");
  comparison.className = "value-comparison";
  comparison.append(
    createValuePair("Application says", field.expected || "Not entered"),
    createValuePair("Label says", field.actual || "Not found"),
  );

  card.append(header, explanation, comparison);
  return card;
}

function createPassRow(field) {
  const row = document.createElement("div");
  row.className = "result-card result-pass";
  const name = document.createElement("span");
  name.className = "pass-name";
  name.textContent = displayFieldName(field.field_name);
  row.append(name, createStatusBadge("PASS"));
  return row;
}

function createStatusBadge(status) {
  const badge = document.createElement("span");
  const statusClass = status.toLowerCase().replaceAll("_", "-").replaceAll(" ", "-");
  badge.className = `status-badge status-${statusClass}`;
  badge.textContent = status;
  return badge;
}

function createValuePair(label, value) {
  const group = document.createElement("div");
  const term = document.createElement("dt");
  const description = document.createElement("dd");
  term.textContent = label;
  description.textContent = value;
  group.append(term, description);
  return group;
}

function displayFieldName(fieldName) {
  return FIELD_LABELS[fieldName] || "Label Information";
}

function failureExplanation(field) {
  if (!field.actual.trim()) {
    return "We could not read this information on the label.";
  }
  if (field.field_name === "government_warning") {
    return "The warning does not match exactly. Check every capital letter, punctuation mark, and space.";
  }
  return "The application and label do not match.";
}

function resetPage() {
  releasePreviewUrl();
  form.reset();
  clearErrors();
  selectedFile.textContent = "No photo selected.";
  imagePreview.hidden = true;
  imagePreview.removeAttribute("src");
  document.getElementById("failed-results").replaceChildren();
  document.getElementById("passed-results").replaceChildren();
  resultsView.hidden = true;
  formView.hidden = false;
  window.scrollTo({ top: 0, behavior: "auto" });
  document.getElementById("page-title").focus();
}

function setMode(mode) {
  const batchMode = mode === "batch";
  form.hidden = batchMode;
  batchForm.hidden = !batchMode;
  singleModeButton.classList.toggle("mode-button-active", !batchMode);
  batchModeButton.classList.toggle("mode-button-active", batchMode);
  singleModeButton.setAttribute("aria-pressed", String(!batchMode));
  batchModeButton.setAttribute("aria-pressed", String(batchMode));
  clearErrors();
  clearBatchErrors();
  (batchMode ? batchForm.querySelector("h2") : document.getElementById("page-title")).focus?.();
}

function addBatchItem(focusCard = false) {
  const count = batchItems.querySelectorAll(".batch-card").length;
  if (count >= 10) return;
  const itemId = nextBatchItemId++;
  const fragment = batchItemTemplate.content.cloneNode(true);
  const card = fragment.querySelector(".batch-card");
  card.dataset.itemId = String(itemId);
  card.querySelectorAll("[data-name]").forEach((control) => {
    const name = control.dataset.name;
    control.id = `batch-${itemId}-${name}`;
    control.setAttribute("aria-describedby", `batch-${itemId}-${name}-error`);
  });
  card.querySelectorAll("[data-for]").forEach((label) => {
    label.htmlFor = `batch-${itemId}-${label.dataset.for}`;
  });
  card.querySelectorAll("[data-error-for]").forEach((error) => {
    error.id = `batch-${itemId}-${error.dataset.errorFor}-error`;
  });
  card.querySelector(".remove-batch-item").addEventListener("click", () => {
    const nextFocus = card.nextElementSibling?.querySelector('[data-name="image"]')
      || card.previousElementSibling?.querySelector('[data-name="image"]')
      || addBatchItemButton;
    card.remove();
    renumberBatchCards();
    nextFocus.focus();
  });
  batchItems.append(card);
  renumberBatchCards();
  if (focusCard) card.querySelector('[data-name="image"]').focus();
}

function renumberBatchCards() {
  const cards = [...batchItems.querySelectorAll(".batch-card")];
  cards.forEach((card, index) => {
    const number = index + 1;
    const title = card.querySelector(".batch-card-title");
    title.textContent = `Label ${number}`;
    title.id = `batch-${card.dataset.itemId}-title`;
    card.setAttribute("aria-labelledby", title.id);
    const removeButton = card.querySelector(".remove-batch-item");
    removeButton.hidden = cards.length === 1;
    removeButton.setAttribute("aria-label", `Remove Label ${number}`);
  });
  addBatchItemButton.disabled = cards.length >= 10;
  addBatchItemButton.textContent = cards.length >= 10
    ? "Maximum of 10 Labels Added"
    : "Add Another Label";
}

function validateBatch(cards) {
  let firstInvalid = null;
  cards.forEach((card) => {
    const image = card.querySelector('[data-name="image"]');
    const file = image.files[0];
    if (!file) {
      firstInvalid = markBatchInvalid(image, "Choose a label photo.", firstInvalid);
    } else if (!ACCEPTED_IMAGE_TYPES.has(file.type)) {
      firstInvalid = markBatchInvalid(image, "Choose a JPEG, PNG, or WEBP photo.", firstInvalid);
    } else if (file.size > MAX_UPLOAD_BYTES) {
      firstInvalid = markBatchInvalid(image, "Choose a photo smaller than 10 MB.", firstInvalid);
    }
    Object.entries(FIELD_LABELS).forEach(([name, label]) => {
      const control = card.querySelector(`[data-name="${name}"]`);
      if (!control.value.trim()) {
        firstInvalid = markBatchInvalid(control, `Enter the ${label}.`, firstInvalid);
      } else if (control.value.length > MAX_TEXT_LENGTHS[name]) {
        firstInvalid = markBatchInvalid(control, `${label} is too long.`, firstInvalid);
      }
    });
    firstInvalid = validateApplicationFormats(card, firstInvalid);
  });
  return firstInvalid;
}

function markBatchInvalid(control, message, firstInvalid) {
  control.setAttribute("aria-invalid", "true");
  control.closest(".batch-card").querySelector(
    `[data-error-for="${control.dataset.name}"]`,
  ).textContent = message;
  return firstInvalid || control;
}

function clearBatchFieldError(control) {
  control.removeAttribute("aria-invalid");
  control.closest(".batch-card").querySelector(
    `[data-error-for="${control.dataset.name}"]`,
  ).textContent = "";
}

function clearBatchErrors() {
  batchItems.querySelectorAll("[data-name]").forEach(clearBatchFieldError);
  errorSummary.hidden = true;
  errorSummary.textContent = "";
}

function setBatchLoading(isLoading, count) {
  batchFieldset.disabled = isLoading;
  if (isLoading) {
    batchProgressText.textContent = `Checking ${count} ${count === 1 ? "label" : "labels"}. Please wait.`;
    batchProgressTimer = window.setTimeout(() => {
      batchProgress.hidden = false;
    }, 400);
    loadingStatus.textContent = `Checking ${count} labels. Please wait.`;
  } else {
    window.clearTimeout(batchProgressTimer);
    batchProgressTimer = null;
    batchProgress.hidden = true;
    loadingStatus.textContent = "";
  }
}

function isBatchResult(payload) {
  return payload
    && Number.isInteger(payload.summary?.passed)
    && Number.isInteger(payload.summary?.needs_review)
    && Number.isInteger(payload.summary?.total)
    && payload.summary.passed + payload.summary.needs_review === payload.summary.total
    && Array.isArray(payload.items)
    && payload.items.length === payload.summary.total
    && payload.items.every((item) => (
      typeof item.filename === "string"
      && ["PASS", "NEEDS_REVIEW", "ERROR"].includes(item.outcome)
      && (item.outcome === "ERROR" || isVerificationResult(item.result))
    ));
}

function renderBatchResults(batch) {
  const summary = document.getElementById("batch-summary");
  summary.replaceChildren(
    createSummaryCount("Passed", batch.summary.passed, "summary-passed"),
    createSummaryCount("Needs Review", batch.summary.needs_review, "summary-review"),
    createSummaryCount("Total", batch.summary.total, "summary-total"),
  );
  document.getElementById("batch-result-items").replaceChildren(
    ...batch.items.map(createBatchResultItem),
  );
  formView.hidden = true;
  resultsView.hidden = true;
  batchResultsView.hidden = false;
  window.scrollTo({ top: 0, behavior: "auto" });
  document.getElementById("batch-results-title").focus();
}

function createSummaryCount(label, value, className) {
  const card = document.createElement("div");
  card.className = `summary-card ${className}`;
  const number = document.createElement("strong");
  number.textContent = String(value);
  const text = document.createElement("span");
  text.textContent = label;
  card.append(number, text);
  return card;
}

function createBatchResultItem(item) {
  const details = document.createElement("details");
  details.className = `batch-result batch-result-${item.outcome.toLowerCase().replace("_", "-")}`;
  const summary = document.createElement("summary");
  const filename = document.createElement("span");
  filename.className = "batch-result-filename";
  filename.textContent = item.filename;
  summary.append(filename, createStatusBadge(item.outcome === "NEEDS_REVIEW" ? "NEEDS REVIEW" : item.outcome));
  const content = document.createElement("div");
  content.className = "batch-result-content";
  if (item.outcome === "ERROR") {
    const error = document.createElement("p");
    error.className = "alert alert-error";
    error.textContent = item.error || "We could not process this label. Please try it again.";
    content.append(error);
  } else {
    const failed = item.result.fields.filter((field) => field.status === "FAIL");
    const passed = item.result.fields.filter((field) => field.status === "PASS");
    if (failed.length) content.append(createBatchFieldGroup("Items to Check", failed.map(createFailureCard)));
    if (passed.length) content.append(createBatchFieldGroup("Items That Match", passed.map(createPassRow)));
  }
  details.append(summary, content);
  return details;
}

function createBatchFieldGroup(titleText, children) {
  const section = document.createElement("section");
  section.className = "batch-detail-section";
  const title = document.createElement("h3");
  title.textContent = titleText;
  const list = document.createElement("div");
  list.className = "result-list";
  list.append(...children);
  section.append(title, list);
  return section;
}

function resetBatchPage() {
  batchForm.reset();
  batchItems.replaceChildren();
  addBatchItem();
  addBatchItem();
  clearBatchErrors();
  document.getElementById("batch-result-items").replaceChildren();
  batchResultsView.hidden = true;
  formView.hidden = false;
  setMode("batch");
  window.scrollTo({ top: 0, behavior: "auto" });
  batchForm.querySelector("h2").setAttribute("tabindex", "-1");
  batchForm.querySelector("h2").focus();
}
