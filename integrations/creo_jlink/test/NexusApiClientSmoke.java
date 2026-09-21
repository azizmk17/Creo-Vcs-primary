import java.util.List;
import java.util.Map;

public final class NexusApiClientSmoke {
    private NexusApiClientSmoke() {
    }

    public static void main(String[] args) throws Exception {
        NexusApiClient api = new NexusApiClient();
        Map<String, Object> context = api.getContext();
        if (!MiniJson.bool(context, "logged_in")) {
            throw new IllegalStateException("Expected a logged-in smoke-test context.");
        }
        Map<String, Object> user = MiniJson.object(context.get("user"));
        if (!"smoke-user".equals(MiniJson.text(user, "username"))) {
            throw new IllegalStateException("Unexpected context response.");
        }

        List<Object> workspaces = api.listWorkspaces();
        if (workspaces.size() != 1) {
            throw new IllegalStateException("Expected one smoke-test workspace.");
        }
        Map<String, Object> created = api.createWorkspace("Created from Java 7");
        if (!"Created from Java 7".equals(MiniJson.text(created, "name"))) {
            throw new IllegalStateException("POST request body was not preserved.");
        }

        System.out.println("JAVA_BRIDGE_SMOKE_OK");
    }
}
