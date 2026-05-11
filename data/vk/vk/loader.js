// лоадер не должен кешироваться и должен быть очень мелким, а сам external.js должен кешироваться и может быть очень большим

// this file is not processed by webpack, therefore can not use process.env

// this will be replaced by devops/docker/entrypoint.sh
const STATIC_HTTP_PREFIX = 'https://prod-msk.vkcloud-static.ru/authapp/master/external-host';

window.solar ??= {};
window.solar.getExtensionUrlCustom = (name) => {
    if (
        name === '@vk-cloud-account/external-host' ||
        name === '_vk_cloud_account_external_host'
    ) {
        return `${STATIC_HTTP_PREFIX.includes('__STATIC_HTTP') ? 'https://local.cloud.vk.com:9001' : STATIC_HTTP_PREFIX}/bundle.js`;
    }

    return null;
};

// eslint-disable-next-line sonarjs/cognitive-complexity
(function () {
    window.__mcsApi__ = {
        ready: async () => {
            if (!window.__authapp_loader_silent_mode__) {
                // eslint-disable-next-line no-console
                console.log(
                    'fake ready called, wait script to be downloaded/installed',
                );
            }
            await new Promise((resolve) => {
                const interval = setInterval(() => {
                    if (window.__mcsApi__.isScriptInstalled) {
                        if (!window.__authapp_loader_silent_mode__) {
                            // eslint-disable-next-line no-console
                            console.log('script is installed');
                        }
                        resolve();
                        clearInterval(interval);
                    }
                }, 100);
            });
            if (!window.__authapp_loader_silent_mode__) {
                // eslint-disable-next-line no-console
                console.log('call real __mcsApi__.ready');
            }
            return window.__mcsApi__.ready();
        },
    };

    const script = document.createElement('script');
    // проверяем была ли констанста изменена(если нет - значит локальный запуск - используем "/")
    script.src = `${STATIC_HTTP_PREFIX.includes('__STATIC_HTTP') ? 'https://local.cloud.vk.com:9001' : `${STATIC_HTTP_PREFIX}`}/external.js`;
    script.crossOrigin = 'anonymous';
    function onLoad() {
        if (!window.__authapp_loader_silent_mode__) {
            // eslint-disable-next-line no-console
            console.log('authapp loader downloaded, install script...');
        }
    }
    function onError() {
        console.error('authapp loader fatal error');
    }
    script.onload = onLoad;
    script.onerror = onError;
    document.head.appendChild(script);
})();
