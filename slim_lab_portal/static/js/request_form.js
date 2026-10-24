// New testing request form. Choices come from GET /api/reference (the SLIM reference cache);
// the form posts JSON to POST /api/submissions and shows the server's field errors.
// Samples are a spreadsheet-like table (paste a column of IDs, Ctrl+D fills down, new rows copy
// the row above). Analyses are chosen in one grouped panel that applies to the ticked rows, or to
// every sample when none are ticked; the selection bar always says which.
// No inline scripts (CSP): this file is loaded with <script src> and wires everything up.
(() => {
  "use strict";

  const form = document.getElementById("request-form");
  if (!form) return;
  const isStaff = form.dataset.staff === "1";
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const $ = (id) => document.getElementById(id);
  const tbody = $("samples");
  const errorBox = $("form-error");

  let ref = null;           // /api/reference payload
  let requestType = null;   // RequestType value (1 Chemical, 2 Water, 3 Wafer)
  let samples = [];         // [{key, sample_name, chemical_name, wafer_size, reporting_unit, processing_time,
                            //   requested_time, additional_element_ids, additional_notes, analyses: Set}]
  const selected = new Set(); // keys of ticked rows; the analysis panel and selection bar act on these
  let panelOpen = true;       // analysis panel expanded, or collapsed to a one-line summary
  let nextKey = 1;

  const TEXT_FIELDS = ["sample_name", "chemical_name", "wafer_size", "reporting_unit", "processing_time",
                       "requested_time", "additional_element_ids", "additional_notes"];

  // ------------------------------------------------------------ helpers

  const el = (tag, attrs = {}, text = "") => {
    const node = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    if (text) node.textContent = text;
    return node;
  };
  const option = (value, label) => el("option", { value: String(value) }, label);
  const fillSelect = (select, items, placeholder) => {
    select.replaceChildren();
    if (placeholder !== undefined) select.append(option("", placeholder));
    items.forEach(([value, label]) => select.append(option(value, label)));
  };
  const splitList = (text) => text.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
  const splitLines = (text) => text.split(/\r?\n/).map((s) => s.split("\t")[0].trim()).filter(Boolean);
  const typeInfo = () => ref.request_types.find((rt) => rt.value === requestType);
  const analysisName = (id) => (typeInfo().analyses.find((a) => a.id === id) || {}).name || `#${id}`;
  const timeLimited = (s) => Number(s.processing_time) === ref.processing_time_requiring_requested_time;

  function radioRow(container, name, items, onChange) {
    container.replaceChildren();
    items.forEach(({ value, label }) => {
      const id = `${name}-${value}`;
      const input = el("input", { type: "radio", name, id, value: String(value) });
      input.addEventListener("change", () => onChange(value));
      const lab = el("label", { for: id, class: "choice" });
      lab.append(input, document.createTextNode(` ${label}`));
      container.append(lab);
    });
  }

  // ------------------------------------------------------------ sample state

  function newSample(name = "") {
    const above = samples[samples.length - 1];
    const sample = { key: nextKey++, sample_name: name, analyses: new Set() };
    TEXT_FIELDS.slice(1).forEach((f) => { sample[f] = above ? above[f] : ""; });
    if (above) sample.analyses = new Set(above.analyses);  // new rows copy the row above
    samples.push(sample);
    return sample;
  }

  function addSamples(names) {
    // Fill an empty last row first, so pasting into a fresh form doesn't leave a blank row.
    const last = samples[samples.length - 1];
    let start = 0;
    if (last && !last.sample_name && names.length) { last.sample_name = names[0]; start = 1; }
    names.slice(start).forEach((n) => newSample(n));
    render();
  }

  const targets = () => (selected.size ? samples.filter((s) => selected.has(s.key)) : samples);

  // ------------------------------------------------------------ table

  function cell(sample, field, control) {
    control.dataset.f = field;
    control.dataset.key = String(sample.key);
    control.value = sample[field] ?? "";
    control.setAttribute("aria-label", field.replace(/_/g, " "));
    const td = el("td", { "data-f": field });
    td.append(control);
    return td;
  }

  function renderHead() {
    const head = $("sample-head");
    head.replaceChildren();
    const all = el("input", { type: "checkbox", id: "select-all", "aria-label": "Select all samples" });
    all.checked = samples.length > 0 && selected.size === samples.length;
    all.indeterminate = selected.size > 0 && selected.size < samples.length;
    const thAll = el("th", { scope: "col" }); thAll.append(all); head.append(thAll);
    head.append(el("th", { scope: "col" }, "#"), el("th", { scope: "col" }, "Sample ID"));
    if (requestType === 1) head.append(el("th", { scope: "col" }, "Chemical / matrix"));
    if (requestType === 3) head.append(el("th", { scope: "col" }, "Wafer size"), el("th", { scope: "col" }, "Units"));
    ["Processing time", "Report by", "Analyses", "Extra elements", "Notes", ""].forEach((t) =>
      head.append(el("th", { scope: "col" }, t)));
  }

  function render() {
    const info = typeInfo();
    renderHead();
    tbody.replaceChildren();
    samples.forEach((s, i) => {
      const tr = el("tr", { "data-key": String(s.key) });
      if (selected.has(s.key)) tr.classList.add("is-selected");
      const pick = el("input", { type: "checkbox", "data-select": String(s.key), "aria-label": `Select sample ${i + 1}` });
      pick.checked = selected.has(s.key);
      const tdPick = el("td"); tdPick.append(pick); tr.append(tdPick);
      tr.append(el("td", { class: "row-no" }, String(i + 1)));
      tr.append(cell(s, "sample_name", el("input", { maxlength: "100", placeholder: "Sample ID" })));
      if (requestType === 1) {
        tr.append(cell(s, "chemical_name", el("input", { maxlength: "200", placeholder: "e.g. IPA" })));
      }
      if (requestType === 3) {
        const size = el("select"); fillSelect(size, ref.wafer_sizes.map((w) => [w.value, w.label]), "Choose…");
        const unit = el("select"); fillSelect(unit, ref.reporting_units.map((u) => [u.value, u.label]), "Choose…");
        tr.append(cell(s, "wafer_size", size), cell(s, "reporting_unit", unit));
      }
      const pt = el("select"); fillSelect(pt, info.processing_times.map((p) => [p.value, p.label]), "Choose…");
      tr.append(cell(s, "processing_time", pt));
      const time = el("input", { type: "time" });
      const tdTime = cell(s, "requested_time", time);
      time.disabled = !timeLimited(s);
      tr.append(tdTime);
      // Clicking a row's analyses selects just that sample.
      const summary = el("td", { "data-f": "analysis_ids", class: "analysis-summary" });
      summary.append(el("button", { type: "button", class: "link-button", "data-only": String(s.key),
                                    title: "Select only this sample" },
                        [...s.analyses].map(analysisName).join(", ") || "None yet"));
      tr.append(summary);
      tr.append(cell(s, "additional_element_ids", el("input", { placeholder: "e.g. Fe, Li", size: "8" })));
      tr.append(cell(s, "additional_notes", el("input", { maxlength: "2000", placeholder: "optional" })));
      const remove = el("button", { type: "button", class: "btn btn--secondary btn--small", "data-remove": String(s.key),
                                    "aria-label": `Remove sample ${i + 1}` }, "✕");
      remove.disabled = samples.length === 1;
      const tdRemove = el("td"); tdRemove.append(remove); tr.append(tdRemove);
      tbody.append(tr);
    });
    renderSelection();
  }

  function renderSelection() {
    const n = selected.size;
    const total = samples.length;
    const bar = $("selection-bar");
    bar.classList.toggle("is-active", n > 0);
    $("selection-actions").hidden = n === 0;
    $("bulk-chemical-wrap").hidden = requestType !== 1;
    $("selection-count").textContent = n
      ? `${n} of ${total} sample${total === 1 ? "" : "s"} selected`
      : `Analyses apply to all ${total} sample${total === 1 ? "" : "s"} — tick rows to choose specific samples`;

    const chosen = targets();
    const names = chosen.map((s) => s.sample_name || `sample ${samples.indexOf(s) + 1}`);
    $("analysis-legend").textContent = n
      ? `Analyses for ${n} selected sample${n === 1 ? "" : "s"}: ${names.slice(0, 4).join(", ")}${n > 4 ? ", …" : ""}`
      : `Analyses for all ${total} sample${total === 1 ? "" : "s"}`;
    $("analysis-panel").classList.toggle("is-targeted", n > 0);
    // Collapsed: one line saying what the targeted samples get, plus "Edit analyses".
    $("analysis-collapsed").hidden = panelOpen;
    $("analysis-body").hidden = !panelOpen;
    if (!panelOpen) {
      const first = chosen[0];
      const same = first && chosen.every((s) => s.analyses.size === first.analyses.size &&
                                               [...s.analyses].every((id) => first.analyses.has(id)));
      $("analysis-summary-text").textContent = !same ? "Different for each sample — see the Analyses column."
        : [...first.analyses].map(analysisName).join(", ") || "None chosen yet.";
    }
    const differ = chosen.some((s) => s.analyses.size !== chosen[0].analyses.size ||
                                      [...s.analyses].some((id) => !chosen[0].analyses.has(id)));
    $("analysis-note").textContent = differ
      ? "These samples have different analyses (partly ticked boxes). Ticking a box gives it to all of them."
      : "";
    document.querySelectorAll("#analysis-groups input").forEach((box) => {
      const id = Number(box.value);
      const count = chosen.filter((s) => s.analyses.has(id)).length;
      box.checked = chosen.length > 0 && count === chosen.length;
      box.indeterminate = count > 0 && count < chosen.length;
    });
  }

  function buildAnalysisPanel() {
    const groups = $("analysis-groups");
    groups.replaceChildren();
    const byGroup = new Map();
    typeInfo().analyses.forEach((a) => {
      if (!byGroup.has(a.group)) byGroup.set(a.group, []);
      byGroup.get(a.group).push(a);
    });
    byGroup.forEach((items, group) => {
      const box = el("div", { class: "analysis-group" });
      box.append(el("h4", {}, group || "Other"));
      items.forEach((a) => {
        const id = `analysis-${a.id}`;
        const input = el("input", { type: "checkbox", id, value: String(a.id) });
        const lab = el("label", { for: id, class: "check" });
        if (a.description && a.description !== a.name) lab.title = a.description;
        lab.append(input, document.createTextNode(` ${a.name}`));
        box.append(lab);
      });
      groups.append(box);
    });
  }

  // ------------------------------------------------------------ events

  const openPanel = () => { panelOpen = true; };
  $("analysis-edit").addEventListener("click", () => { panelOpen = true; renderSelection(); });
  $("analysis-done").addEventListener("click", () => {
    panelOpen = false;
    renderSelection();
    $("analysis-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
  });

  $("analysis-groups").addEventListener("change", (event) => {
    const id = Number(event.target.value);
    targets().forEach((s) => (event.target.checked ? s.analyses.add(id) : s.analyses.delete(id)));
    render();
  });

  $("sample-head").addEventListener("change", (event) => {
    if (event.target.id !== "select-all") return;
    samples.forEach((s) => (event.target.checked ? selected.add(s.key) : selected.delete(s.key)));
    if (event.target.checked) openPanel();
    render();
  });

  tbody.addEventListener("input", (event) => {
    const { f, key } = event.target.dataset;
    if (!f) return;
    const sample = samples.find((s) => String(s.key) === key);
    sample[f] = event.target.value;
    if (f === "processing_time") {
      if (!timeLimited(sample)) sample.requested_time = "";
      const time = event.target.closest("tr").querySelector('[data-f="requested_time"] input');
      time.disabled = !timeLimited(sample);
      time.value = sample.requested_time;
    }
    if (f === "sample_name" && selected.has(sample.key)) renderSelection();
  });
  tbody.addEventListener("change", (event) => {
    if (!event.target.dataset.select) return;
    const key = Number(event.target.dataset.select);
    if (event.target.checked) { selected.add(key); openPanel(); } else selected.delete(key);
    render();
  });
  tbody.addEventListener("click", (event) => {
    if (event.target.dataset.only) {
      selected.clear();
      selected.add(Number(event.target.dataset.only));
      openPanel();
      render();
      $("analysis-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
      return;
    }
    const key = event.target.dataset.remove;
    if (!key || samples.length === 1) return;
    samples = samples.filter((s) => String(s.key) !== key);
    selected.delete(Number(key));
    render();
  });
  // Paste a column of IDs into a Sample ID cell: one row per line, starting at that row.
  tbody.addEventListener("paste", (event) => {
    if (event.target.dataset.f !== "sample_name") return;
    const names = splitLines(event.clipboardData.getData("text"));
    if (names.length < 2) return;
    event.preventDefault();
    const index = samples.findIndex((s) => String(s.key) === event.target.dataset.key);
    names.forEach((name, i) => {
      if (index + i < samples.length) samples[index + i].sample_name = name;
      else newSample(name);
    });
    render();
  });
  // Ctrl+D (Cmd+D on a Mac): copy this cell's value from the row above, like Excel's fill down.
  tbody.addEventListener("keydown", (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "d") return;
    const { f, key } = event.target.dataset;
    if (!f) return;
    event.preventDefault();
    const index = samples.findIndex((s) => String(s.key) === key);
    if (index < 1) return;
    samples[index][f] = samples[index - 1][f];
    if (f === "processing_time" && !timeLimited(samples[index])) samples[index].requested_time = "";
    render();
    const again = tbody.querySelector(`[data-key="${key}"][data-f="${f}"]`);
    if (again) again.focus();
  });

  // selection bar
  const selectedSamples = () => samples.filter((s) => selected.has(s.key));
  $("bulk-processing-time").addEventListener("change", (event) => {
    if (!event.target.value) return;
    selectedSamples().forEach((s) => {
      s.processing_time = event.target.value;
      if (!timeLimited(s)) s.requested_time = "";
    });
    event.target.value = "";
    render();
  });
  $("bulk-chemical").addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const value = event.target.value.trim();
    if (!value) return;
    selectedSamples().forEach((s) => { s.chemical_name = value; });
    event.target.value = "";
    render();
  });
  $("bulk-duplicate").addEventListener("click", () => {
    const copies = selectedSamples().map((s) => ({
      ...s, key: nextKey++, sample_name: s.sample_name ? `${s.sample_name} (copy)` : "", analyses: new Set(s.analyses),
    }));
    samples.push(...copies);
    selected.clear();
    copies.forEach((c) => selected.add(c.key));
    render();
  });
  $("bulk-remove").addEventListener("click", () => {
    const remaining = samples.filter((s) => !selected.has(s.key));
    if (!remaining.length) { showErrors([{ text: "A request needs at least one sample." }]); return; }
    samples = remaining;
    selected.clear();
    render();
  });
  $("bulk-clear").addEventListener("click", () => {
    selected.clear();
    if (samples[0] && samples[0].analyses.size) panelOpen = false;
    render();
  });

  $("add-sample").addEventListener("click", () => {
    newSample();
    render();
    tbody.lastElementChild.querySelector('input[data-f="sample_name"]').focus();
  });
  $("add-pasted").addEventListener("click", () => {
    const names = splitLines($("paste-ids").value);
    if (!names.length) return;
    addSamples(names);
    $("paste-ids").value = "";
  });

  function setRequestType(value) {
    const changed = requestType !== null && requestType !== value;
    requestType = value;
    $("samples-section").hidden = false;
    $("submit-button").disabled = false;
    const info = typeInfo();
    const allowedTimes = new Set(info.processing_times.map((p) => String(p.value)));
    const allowedAnalyses = new Set(info.analyses.map((a) => a.id));
    samples.forEach((s) => {   // keep what still applies to the new type
      if (!allowedTimes.has(String(s.processing_time))) s.processing_time = "";
      s.analyses = new Set([...s.analyses].filter((id) => allowedAnalyses.has(id)));
      if (changed && value !== 1) s.chemical_name = "";
      if (changed) { s.wafer_size = ""; s.reporting_unit = ""; }
    });
    if (!samples.length) newSample();
    fillSelect($("bulk-processing-time"), info.processing_times.map((p) => [p.value, p.label]), "Set…");
    buildAnalysisPanel();
    render();
  }

  // ------------------------------------------------------------ customer, payment, locations

  function setLocations(locations) {
    $("location-field").classList.toggle("hidden", !locations.length);
    fillSelect($("location"), locations.map((l) => [l, l]), locations.length ? "Choose a site…" : "");
  }

  function setPayment(value) {
    const po = value === 1;
    $("po-field").classList.toggle("hidden", !po);
    $("card-hint").classList.toggle("hidden", po);
    if (!po) $("po_number").value = "";
  }

  async function prefillFromLastRequest() {
    // Customers: start from their most recent request's contact and distribution lists.
    const response = await fetch("/api/submissions?per_page=1", { headers: { Accept: "application/json" } });
    if (!response.ok) return;
    const last = (await response.json()).items[0];
    if (!last) return;
    ["customer_contact", "customer_phone"].forEach((f) => { $(f).value = last[f]; });
    ["results_to", "results_cc", "invoice_to", "invoice_cc"].forEach((f) => { $(f).value = last[f].join(", "); });
    const pay = $(`payment_method-${last.payment_method}`);
    if (pay) { pay.checked = true; setPayment(last.payment_method); }
    $("po_number").value = last.po_number;
    if (last.location) $("location").value = last.location;
  }

  // ------------------------------------------------------------ submit

  function collect() {
    const problems = [];
    const intOrNull = (v) => (v === "" || v === undefined || v === null ? null : Number(v));
    const symbols = new Map(ref.elements.map((e) => [e.symbol.toLowerCase(), e.id]));
    const body = {
      request_type: requestType,
      location: $("location").value,
      expected_arrival_date: $("expected_arrival_date").value || null,
      customer_contact: $("customer_contact").value.trim(),
      customer_phone: $("customer_phone").value.trim(),
      payment_method: intOrNull((document.querySelector('input[name="payment_method"]:checked') || {}).value),
      po_number: $("po_number").value.trim(),
      results_to: splitList($("results_to").value),
      results_cc: splitList($("results_cc").value),
      invoice_to: splitList($("invoice_to").value),
      invoice_cc: splitList($("invoice_cc").value),
      samples: samples.map((s, i) => {
        const elementIds = splitList(s.additional_element_ids || "").map((sym) => {
          const id = symbols.get(sym.toLowerCase());
          if (id === undefined) problems.push({ text: `Sample ${i + 1}: "${sym}" is not an element symbol.`,
                                                sample: i, field: "additional_element_ids" });
          return id;
        });
        const sample = {
          sample_name: (s.sample_name || "").trim(),
          processing_time: intOrNull(s.processing_time),
          requested_time: timeLimited(s) ? s.requested_time || null : null,
          additional_notes: (s.additional_notes || "").trim(),
          analysis_ids: [...s.analyses],
          additional_element_ids: elementIds.filter((id) => id !== undefined),
        };
        if (requestType === 1) sample.chemical_name = (s.chemical_name || "").trim();
        if (requestType === 3) {
          sample.wafer_size = intOrNull(s.wafer_size);
          sample.reporting_unit = intOrNull(s.reporting_unit);
        }
        return sample;
      }),
    };
    if (isStaff) body.customer_id = intOrNull($("customer_id").value);
    return { body, problems };
  }

  const FIELD_LABELS = {
    customer_id: "Customer", request_type: "Request type", location: "Site", customer_contact: "Contact name",
    customer_phone: "Phone", payment_method: "Payment", po_number: "PO number", results_to: "Results to",
    results_cc: "Results cc", invoice_to: "Invoice to", invoice_cc: "Invoice cc",
    expected_arrival_date: "Expected arrival", sample_name: "Sample ID", chemical_name: "Chemical",
    processing_time: "Processing time", requested_time: "Report-by time", analysis_ids: "Analyses",
    additional_element_ids: "Extra elements", wafer_size: "Wafer size", reporting_unit: "Units",
    additional_notes: "Notes", samples: "Samples",
  };

  // Plain-language versions of the validator's most common messages.
  function friendly(msg) {
    const m = msg.replace(/^Value error, /, "");
    if (/^Field required$/.test(m) || /valid integer/.test(m) || /at least 1 character/.test(m)) return "is required";
    if (/at least 1 item/.test(m)) return "needs at least one entry";
    if (/valid email address/.test(m)) return "contains an address that isn't a valid email";
    return m;
  }

  function describe(loc) {
    const path = loc[0] === "body" ? loc.slice(1) : loc;
    if (path[0] === "samples" && Number.isInteger(path[1])) {
      const field = path[2] !== undefined ? ` – ${FIELD_LABELS[path[2]] || path[2]}` : "";
      return { text: `Sample ${path[1] + 1}${field}`, sample: path[1], field: path[2] };
    }
    return { text: path.length ? FIELD_LABELS[path[0]] || path[0] : "Request", field: path[0] };
  }

  function showErrors(messages) {
    form.querySelectorAll(".has-error").forEach((n) => n.classList.remove("has-error"));
    errorBox.replaceChildren(el("strong", {}, "Please fix the following:"));
    const list = el("ul");
    messages.forEach(({ text, sample, field }) => {
      list.append(el("li", {}, text));
      let target = null;
      if (sample !== undefined) {
        const row = tbody.children[sample];
        target = row && (field ? row.querySelector(`td[data-f="${field}"]`) : row);
      } else if (field) {
        target = form.querySelector(`[data-path="${field}"]`);
      }
      if (target) (target.closest(".field") || target).classList.add("has-error");
    });
    errorBox.append(list);
    errorBox.classList.remove("hidden");
    errorBox.focus();
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const { body, problems } = collect();
    if (problems.length) { showErrors(problems); return; }
    const button = $("submit-button");
    button.disabled = true;
    try {
      const response = await fetch("/api/submissions", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      if (response.status === 201) {
        window.location.assign(`/requests/${encodeURIComponent(data.tr_number)}`);
        return;
      }
      if (Array.isArray(data.detail)) {
        showErrors(data.detail.map((d) => {
          const where = describe(d.loc || []);
          return { ...where, text: `${where.text}: ${friendly(d.msg)}` };
        }));
      } else {
        showErrors([{ text: data.detail || `The request could not be submitted (HTTP ${response.status}).` }]);
      }
    } catch (err) {
      showErrors([{ text: "Could not reach the server. Check your connection and try again." }]);
    } finally {
      button.disabled = false;
    }
  });

  // ------------------------------------------------------------ start

  (async () => {
    try {
      const response = await fetch("/api/reference", { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      ref = await response.json();
    } catch (err) {
      showErrors([{ text: "The lab's analysis list could not be loaded. Please reload the page." }]);
      return;
    }
    $("form-loading").remove();
    radioRow($("request-types"), "request_type", ref.request_types, setRequestType);
    radioRow($("payment-methods"), "payment_method", ref.payment_methods, setPayment);
    $("payment_method-1").checked = true;
    setPayment(1);
    const today = new Date();
    $("expected_arrival_date").min = new Date(today.getTime() - today.getTimezoneOffset() * 60000)
      .toISOString().slice(0, 10);
    if (isStaff) {
      const select = $("customer_id");
      ref.customers.forEach((c) => select.append(option(c.id, c.name)));
      select.addEventListener("change", () => {
        const customer = ref.customers.find((c) => String(c.id) === select.value);
        setLocations(customer ? customer.locations : []);
      });
      setLocations([]);
    } else {
      setLocations(ref.locations);
      await prefillFromLastRequest();
    }
  })();
})();
