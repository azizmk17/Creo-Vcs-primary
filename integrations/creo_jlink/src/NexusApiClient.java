import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URLEncoder;
import java.net.URL;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class NexusApiClient {
    private static final int CONNECT_TIMEOUT_MS = 1500;
    private static final int READ_TIMEOUT_MS = 5000;
    private static final int MUTATION_READ_TIMEOUT_MS = 120000;

    public Map<String, Object> getContext() throws Exception {
        return request("GET", "/context", null);
    }

    public List<Object> listWorkspaces() throws Exception {
        return MiniJson.array(request("GET", "/workspaces", null).get("workspaces"));
    }

    public Map<String, Object> createWorkspace(String name) throws Exception {
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("name", name);
        return MiniJson.object(request("POST", "/workspaces", body).get("workspace"));
    }

    public List<Object> listWorkspaceCheckouts(String workspaceId) throws Exception {
        return MiniJson.array(listWorkspaceState(workspaceId).get("cad_documents"));
    }

    public Map<String, Object> listWorkspaceState(String workspaceId) throws Exception {
        String encoded = URLEncoder.encode(workspaceId == null ? "" : workspaceId, "UTF-8");
        return request("GET", "/workspaces/" + encoded + "/checkouts", null);
    }

    public Map<String, Object> resolveCad(String fileName) throws Exception {
        String encoded = URLEncoder.encode(fileName, "UTF-8");
        return MiniJson.object(request("GET", "/cad/resolve?file_name=" + encoded, null).get("cad"));
    }

    public List<Object> listCadDocuments() throws Exception {
        return MiniJson.array(request("GET", "/cad", null).get("cad_documents"));
    }

    public Map<String, Object> cadStatus(int cadId) throws Exception {
        return MiniJson.object(request("GET", "/cad/" + cadId, null).get("cad"));
    }

    public Map<String, Object> cadHistory(int cadId) throws Exception {
        return request("GET", "/cad/" + cadId + "/history", null);
    }

    public Map<String, Object> revise(int cadId) throws Exception {
        return request("POST", "/cad/" + cadId + "/revise", new LinkedHashMap<String, Object>());
    }

    public Map<String, Object> release(int cadId) throws Exception {
        return request("POST", "/cad/" + cadId + "/release", new LinkedHashMap<String, Object>());
    }

    public Map<String, Object> retrieve(int cadId, String workspaceId) throws Exception {
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("workspace_id", workspaceId);
        body.put("include_drawings", Boolean.TRUE);
        body.put("include_dependencies", Boolean.TRUE);
        return request("POST", "/cad/" + cadId + "/retrieve", body);
    }

    public Map<String, Object> checkout(
        int cadId,
        String workspaceId,
        boolean reviseReleased,
        String releasedItemRevision
    ) throws Exception {
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("workspace_id", workspaceId);
        body.put("include_drawings", Boolean.TRUE);
        body.put("include_dependencies", Boolean.TRUE);
        body.put("revise_released", Boolean.valueOf(reviseReleased));
        if (releasedItemRevision != null && releasedItemRevision.trim().length() > 0) {
            body.put("released_item_revision_code", releasedItemRevision.trim());
        }
        return request("POST", "/cad/" + cadId + "/checkout", body);
    }

    public Map<String, Object> checkin(
        int cadId, String workspaceId, String path, String note
    ) throws Exception {
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("workspace_id", workspaceId);
        body.put("path", path == null ? "" : path);
        body.put("note", note);
        return request("POST", "/cad/" + cadId + "/checkin", body);
    }

    public Map<String, Object> undoCheckout(int cadId, String note) throws Exception {
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("note", note);
        return request("POST", "/cad/" + cadId + "/undo", body);
    }

    public Map<String, Object> request(String method, String path, Map<String, Object> body)
        throws Exception {
        BridgeConnection bridge = loadConnection();
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(bridge.apiUrl + path).openConnection();
            connection.setRequestMethod(method);
            connection.setConnectTimeout(CONNECT_TIMEOUT_MS);
            connection.setReadTimeout(body == null ? READ_TIMEOUT_MS : MUTATION_READ_TIMEOUT_MS);
            connection.setUseCaches(false);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("Connection", "close");
            connection.setRequestProperty("X-Nexus-Token", bridge.token);
            if (body != null) {
                byte[] payload = MiniJson.stringify(body).getBytes("UTF-8");
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                connection.setFixedLengthStreamingMode(payload.length);
                OutputStream output = connection.getOutputStream();
                try {
                    output.write(payload);
                } finally {
                    output.close();
                }
            }

            int status = connection.getResponseCode();
            InputStream stream = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
            String responseText = stream == null ? "" : readAll(stream);
            Map<String, Object> envelope = MiniJson.object(MiniJson.parse(responseText));
            if (status >= 400 || !MiniJson.bool(envelope, "ok")) {
                Map<String, Object> error = envelope.get("error") instanceof Map
                    ? MiniJson.object(envelope.get("error"))
                    : new LinkedHashMap<String, Object>();
                throw new NexusApiException(
                    status,
                    MiniJson.text(error, "code"),
                    MiniJson.text(error, "message").length() == 0
                        ? "Nexus bridge request failed."
                        : MiniJson.text(error, "message")
                );
            }
            return MiniJson.object(envelope.get("data"));
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    public File connectionFile() {
        String override = System.getenv("NEXUS_CREO_BRIDGE_FILE");
        if (override != null && override.trim().length() > 0) {
            return new File(override.trim());
        }
        String local = System.getenv("LOCALAPPDATA");
        if (local == null || local.trim().length() == 0) {
            local = System.getProperty("user.home") + File.separator + "AppData" + File.separator + "Local";
        }
        return new File(new File(local, "CreoVCS"), "bridge.json");
    }

    private BridgeConnection loadConnection() throws Exception {
        File path = connectionFile();
        if (!path.isFile()) {
            throw new IOException(
                "Nexus bridge is not running. Start Nexus, sign in, and select a product version."
            );
        }
        FileInputStream input = new FileInputStream(path);
        String content;
        try {
            content = readAll(input);
        } finally {
            input.close();
        }
        Map<String, Object> value = MiniJson.object(MiniJson.parse(content));
        String apiUrl = MiniJson.text(value, "api_url");
        String token = MiniJson.text(value, "token");
        if (apiUrl.length() == 0 || token.length() == 0) {
            throw new IOException("The Nexus bridge connection file is incomplete.");
        }
        URL endpoint = new URL(apiUrl);
        String host = endpoint.getHost();
        if (!"http".equalsIgnoreCase(endpoint.getProtocol())
            || !("127.0.0.1".equals(host) || "localhost".equalsIgnoreCase(host))) {
            throw new IOException("The Nexus bridge connection must use localhost HTTP.");
        }
        return new BridgeConnection(apiUrl, token);
    }

    private static String readAll(InputStream input) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        int count;
        while ((count = input.read(buffer)) >= 0) {
            output.write(buffer, 0, count);
        }
        return new String(output.toByteArray(), "UTF-8");
    }

    private static final class BridgeConnection {
        private final String apiUrl;
        private final String token;

        private BridgeConnection(String apiUrl, String token) {
            this.apiUrl = apiUrl;
            this.token = token;
        }
    }
}
