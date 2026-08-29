/* Paste your Firebase web config here to turn on online drafting.
 *
 * Setup, once, about three minutes:
 *
 *   1. console.firebase.google.com  ->  Add project (no card needed)
 *   2. Build -> Realtime Database -> Create Database
 *        - pick a location
 *        - start in **locked mode**; the rules below replace it
 *   3. Rules tab -> paste the contents of firebase-rules.json -> Publish
 *   4. Project settings (gear) -> Your apps -> Web (</>) -> register the app
 *   5. Copy the `firebaseConfig` values it shows you into the object below
 *
 * The apiKey and databaseURL are meant to be public in a web page -- they
 * identify the project, they do not grant access. The database rules are what
 * actually control who can write, which is why step 3 is not optional.
 *
 * Leave this file as-is and the app simply hides online mode; solo and
 * same-screen play still work with no setup at all.
 */
window.OTC_FIREBASE = {
  apiKey: "",
  databaseURL: "",   // looks like https://<project>-default-rtdb.firebaseio.com
  projectId: "",
};
