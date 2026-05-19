// AgentForge branding — override Chainlit default favicon
(function () {
    function setFavicon() {
        let link = document.querySelector("link[rel~='icon']");
        if (!link) {
            link = document.createElement("link");
            document.head.appendChild(link);
        }
        link.type = "image/svg+xml";
        link.rel = "icon";
        link.href = "/public/favicon.svg";
    }
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", setFavicon);
    } else {
        setFavicon();
    }
})();
