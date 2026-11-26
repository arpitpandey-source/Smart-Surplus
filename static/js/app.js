// SmartSurplus progressive enhancement script (CSP script-src 'self' compliant)
document.addEventListener("DOMContentLoaded", function () {
    const toggleBtn = document.querySelector(".nav-toggle");
    const navCollapse = document.getElementById("primary-navigation");
    if (toggleBtn && navCollapse) {
        toggleBtn.addEventListener("click", function () {
            const isOpen = navCollapse.classList.toggle("nav-open");
            toggleBtn.setAttribute("aria-expanded", String(isOpen));
        });
    }
});

document.addEventListener("submit", function (event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) {
        return;
    }

    const confirmMsg = form.getAttribute("data-confirm");
    if (confirmMsg && !window.confirm(confirmMsg)) {
        event.preventDefault();
        return;
    }

    const submitBtn = form.querySelector("button[type='submit']");
    if (!submitBtn) {
        return;
    }

    if (form.classList.contains("claim-form") || form.hasAttribute("data-submit-label")) {
        if (typeof form.checkValidity === "function" && !form.checkValidity()) {
            return;
        }
        if (submitBtn.dataset.submitted === "1") {
            event.preventDefault();
            return;
        }
        submitBtn.dataset.originalLabel = submitBtn.textContent;
        submitBtn.dataset.submitted = "1";
        submitBtn.disabled = true;
        submitBtn.setAttribute("aria-busy", "true");
        submitBtn.textContent = form.getAttribute("data-submit-label") || "Reserving...";
    }
});

// Restore submit buttons if navigating back via browser bfcache
window.addEventListener("pageshow", function () {
    document.querySelectorAll("button[data-submitted='1']").forEach(function (btn) {
        btn.disabled = false;
        btn.removeAttribute("aria-busy");
        delete btn.dataset.submitted;
        if (btn.dataset.originalLabel) {
            btn.textContent = btn.dataset.originalLabel;
        }
    });
});
