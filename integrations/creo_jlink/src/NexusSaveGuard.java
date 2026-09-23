import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.util.Map;

import com.ptc.cipjava.jxthrowable;
import com.ptc.pfc.pfcCommand.DefaultUICommandBracketListener;
import com.ptc.pfc.pfcExceptions.XCancelProEAction;
import com.ptc.pfc.pfcModel.Model;
import com.ptc.pfc.pfcSession.Session;

public class NexusSaveGuard extends DefaultUICommandBracketListener {
    private final Session session;
    private final NexusApiClient api;
    private final String action;

    public NexusSaveGuard(Session session, NexusApiClient api, String action) {
        this.session = session;
        this.api = api;
        this.action = action;
    }

    public void OnBeforeCommand() throws jxthrowable {
        Model model = session.GetCurrentModel();
        if (model == null) {
            model = session.GetActiveModel();
        }
        if (model != null) {
            enforce(model);
        }
    }

    public void OnAfterCommand() throws jxthrowable {
    }

    private void enforce(Model model) throws jxthrowable {
        String fileName = model.GetFileName();
        if (fileName == null || fileName.trim().length() == 0) {
            return;
        }

        boolean block = false;
        String reason = "";
        try {
            Map<String, Object> status = NexusJLink.resolveCadForModel(model);
            if (!MiniJson.bool(status, "managed")) {
                if (isTrackedWorkspaceFile(model, fileName)) {
                    block = true;
                    reason = "The file belongs to a Nexus workspace, but the active Nexus project does not match it.";
                }
            } else if (!MiniJson.bool(status, "can_modify")
                && !MiniJson.bool(status, "local_edit_intent")) {
                block = true;
                reason = MiniJson.text(status, "read_only_reason");
                String owner = MiniJson.text(status, "checked_out_by_username");
                if (owner.length() > 0 && "CHECKED_OUT_BY_OTHER".equals(MiniJson.text(status, "checkout_state"))) {
                    reason = "Checked out by " + owner + ".";
                }
            } else if (!modelIsInsideWorkspace(model, MiniJson.text(status, "workspace_path"))) {
                block = true;
                reason = "Open the checked-out copy from its assigned Nexus CAD workspace before editing.";
            } else if (MiniJson.bool(status, "local_edit_intent")) {
                // Local intent permits a local Save; server check-in remains blocked.
                return;
            }
        } catch (Exception error) {
            if (isTrackedWorkspaceFile(model, fileName)) {
                block = true;
                reason = "Nexus could not verify checkout ownership: " + error.getMessage();
            }
        }

        if (block) {
            NexusDialogs.warningLater(
                "Nexus blocked the " + action + " operation for " + fileName + ".\n\n" + reason,
                "Nexus PDM"
            );
            XCancelProEAction.Throw();
        }
    }

    private boolean modelIsInsideWorkspace(Model model, String workspacePath) {
        if (workspacePath == null || workspacePath.trim().length() == 0) {
            return false;
        }
        File origin = modelOrigin(model);
        if (origin == null) {
            return false;
        }
        try {
            String root = new File(workspacePath).getCanonicalPath();
            String candidate = origin.getCanonicalPath();
            return candidate.equalsIgnoreCase(root)
                || candidate.toLowerCase().startsWith((root + File.separator).toLowerCase());
        } catch (Exception ignored) {
            return false;
        }
    }

    private File modelOrigin(Model model) {
        try {
            String value = model == null ? "" : model.GetOrigin();
            if (value == null || value.trim().length() == 0) {
                return null;
            }
            File origin = new File(value.trim());
            if (!origin.isAbsolute()) {
                return null;
            }
            return origin;
        } catch (Throwable ignored) {
            return null;
        }
    }

    private boolean isTrackedWorkspaceFile(Model model, String fileName) {
        File origin = modelOrigin(model);
        File directory = origin;
        if (directory == null) {
            return false;
        }
        if (!new File(directory, ".creovcs-workspace.json").isFile()) {
            directory = directory.getParentFile();
        }
        if (directory == null) {
            return false;
        }
        File manifest = new File(directory, ".creovcs-workspace.json");
        if (!manifest.isFile()) {
            return false;
        }
        InputStream input = null;
        try {
            input = new FileInputStream(manifest);
            byte[] buffer = new byte[(int) Math.min(manifest.length(), 1024L * 1024L)];
            int offset = 0;
            while (offset < buffer.length) {
                int count = input.read(buffer, offset, buffer.length - offset);
                if (count < 0) break;
                offset += count;
            }
            Map<String, Object> root = MiniJson.object(
                MiniJson.parse(new String(buffer, 0, offset, "UTF-8"))
            );
            Map<String, Object> entries = MiniJson.object(root.get("entries"));
            String logical = logicalName(fileName);
            for (Object raw : entries.values()) {
                Map<String, Object> entry = MiniJson.object(raw);
                if (logical.equalsIgnoreCase(MiniJson.text(entry, "logical_file_name"))) {
                    return true;
                }
            }
        } catch (Exception ignored) {
            return true;
        } finally {
            if (input != null) {
                try { input.close(); } catch (Exception ignored) { }
            }
        }
        return false;
    }

    private static String logicalName(String fileName) {
        String name = new File(fileName == null ? "" : fileName).getName();
        return name.replaceFirst("(?i)\\.(prt|asm|drw)\\.\\d+$", ".$1");
    }
}
