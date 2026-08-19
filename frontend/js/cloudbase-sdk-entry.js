import cloudbase from "@cloudbase/js-sdk/app";
import { registerAuth } from "@cloudbase/js-sdk/auth";

// Keep the browser bundle focused on authentication; market browsing uses plain fetch.
registerAuth(cloudbase);
window.CloudBaseSDK = cloudbase;
