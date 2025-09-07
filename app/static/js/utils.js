// Credits: https://stackoverflow.com/a/71177790/2087442
// Modified to parse nested JSON-encoded string objects
function flattenObject(obj) {
        if (obj === undefined) return {};
        const object = Object.create(null);
        const path = [];
        const isObject = (value) => Object(value) === value;

        function dig(obj) {
                for (let [key, value] of Object.entries(obj)) {
                        // Check if the value is actually a JSON-encoded string holding an object
                        if (typeof value === "string" && ((value.startsWith("{") && value.endsWith("}")) || (value.startsWith("[") && value.endsWith("]")))) {
                                try {
                                        value = JSON.parse(value);
                                } catch (e) {
                                        // If parsing fails, leave it as-is
                                }
                        }
                        path.push(key);
                        if (isObject(value)) dig(value);
                        else object[path.join('.')] = value;
                        path.pop();
                }
        }

        dig(obj);
        return object;
}

// Credits: https://stackoverflow.com/a/71177790/2087442
// Modified to return added, changed, and removed changes instead of only
// changed and removed
function diffFlatten(before, after) {
        const added = Object.assign({}, after);
        const changed = Object.assign({}, after);
        const removed = Object.assign({}, before);

        /**delete the unUpdated keys*/
        for (let key in after) {
                if (after[key] === before[key]) {
                        // Stayed the same -> remove everywhere
                        delete added[key];
                        delete changed[key];
                        delete removed[key];
                } else if (key in before) {
                        // Was there before -> keep only in changed
                        delete added[key];
                        changed[key] = { from: before[key], to: after[key] };
                        delete removed[key];
                } else {
                        // Wasn't there before -> keep only in added
                        delete changed[key];
                        delete removed[key];
                }
        }

        return [added, changed, removed];

}

function compareObjects(before, after) {
        const [added, changed, removed] = diffFlatten(flattenObject(before), flattenObject(after));
        return { "added": added, "changed": changed, "removed": removed };
}
