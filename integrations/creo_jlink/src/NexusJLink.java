import java.io.File;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import javax.swing.JOptionPane;

import com.ptc.cipjava.jxthrowable;
import com.ptc.pfc.pfcCommand.DefaultUICommandActionListener;
import com.ptc.pfc.pfcCommand.UICommand;
import com.ptc.pfc.pfcGlobal.pfcGlobal;
import com.ptc.pfc.pfcModel.Dependencies;
import com.ptc.pfc.pfcModel.Dependency;
import com.ptc.pfc.pfcModel.Model;
import com.ptc.pfc.pfcModel.ModelDescriptor;
import com.ptc.pfc.pfcModel.pfcModel;
import com.ptc.pfc.pfcSession.Session;

public class NexusJLink {
    private static Session session;
    private static NexusApiClient api;
    private static final List<CommandGuardRegistration> commandGuards =
        new ArrayList<CommandGuardRegistration>();
    private static Map<String, Object> selectedWorkspace;

    public static void start() {
        try {
            session = pfcGlobal.GetProESession();
            api = new NexusApiClient();
            installCommandGuards();
            installMenu();
            System.out.println("Nexus PDM J-Link started.");
        } catch (Throwable error) {
            showError(error);
        }
    }

    public static void stop() {
        for (CommandGuardRegistration registration : commandGuards) {
            try {
                registration.command.RemoveActionListener(registration.listener);
            } catch (Throwable ignored) {
            }
        }
        commandGuards.clear();
        selectedWorkspace = null;
        api = null;
        session = null;
    }

    private static void installCommandGuards() throws jxthrowable {
        addCommandGuard("ProCmdModelSave", "save");
        addCommandGuard("ProCmdModelRename", "rename");
    }

    private static void addCommandGuard(String commandName, String action) throws jxthrowable {
        UICommand command = session.UIGetCommand(commandName);
        if (command == null) {
            return;
        }
        NexusSaveGuard listener = new NexusSaveGuard(session, api, action);
        command.AddActionListener(listener);
        commandGuards.add(new CommandGuardRegistration(command, listener));
    }

    private static void installMenu() throws jxthrowable {
        session.UIAddMenu("NexusPDM", "Applications", "nexus_jlink.txt", null);
        addCommand("NexusPDM.Connect", "NexusConnect", "NexusConnectHelp", new CommandAction() {
            public void run() throws Exception { connect(); }
        });
        addCommand("NexusPDM.Workspace", "NexusWorkspace", "NexusWorkspaceHelp", new CommandAction() {
            public void run() throws Exception { chooseWorkspace(); }
        });
        addCommand("NexusPDM.Retrieve", "NexusRetrieve", "NexusRetrieveHelp", new CommandAction() {
            public void run() throws Exception { retrieve(); }
        });
        addCommand("NexusPDM.Status", "NexusStatus", "NexusStatusHelp", new CommandAction() {
            public void run() throws Exception { showStatus(); }
        });
        addCommand("NexusPDM.Checkout", "NexusCheckout", "NexusCheckoutHelp", new CommandAction() {
            public void run() throws Exception { checkout(); }
        });
        addCommand("NexusPDM.Checkin", "NexusCheckin", "NexusCheckinHelp", new CommandAction() {
            public void run() throws Exception { checkin(); }
        });
        addCommand("NexusPDM.Undo", "NexusUndo", "NexusUndoHelp", new CommandAction() {
            public void run() throws Exception { undoCheckout(); }
        });
    }

    private static void addCommand(
        String commandName, String labelKey, String helpKey, CommandAction action
    ) throws jxthrowable {
        UICommand command = session.UICreateCommand(commandName, new CommandListener(action));
        session.UIAddButton(
            command, "NexusPDM", null, labelKey, helpKey, "nexus_jlink.txt"
        );
    }

    private static void connect() throws Exception {
        Map<String, Object> context = requireContext();
        Map<String, Object> user = MiniJson.object(context.get("user"));
        Map<String, Object> project = MiniJson.object(context.get("project"));
        String workspaceText = selectedWorkspace == null
            ? "Not selected"
            : MiniJson.text(selectedWorkspace, "name") + "\n" + MiniJson.text(selectedWorkspace, "path");
        NexusDialogs.info(
            "Connected to Nexus.\n\nUser: " + MiniJson.text(user, "username")
                + "\nProduct: " + projectLabel(project)
                + "\nWorkspace: " + workspaceText,
            "Nexus PDM"
        );
    }

    private static Map<String, Object> requireContext() throws Exception {
        Map<String, Object> context = api.getContext();
        if (!MiniJson.bool(context, "logged_in")) {
            throw new IllegalStateException("Sign in to Nexus before using the Creo integration.");
        }
        if (!(context.get("project") instanceof Map)) {
            throw new IllegalStateException("Select a product and version in Nexus first.");
        }
        return context;
    }

    private static String projectLabel(Map<String, Object> project) {
        String number = MiniJson.text(project, "product_number");
        String name = MiniJson.text(project, "name");
        String version = MiniJson.text(project, "version_label");
        String label = number.length() > 0 ? number + " - " + name : name;
        return version.length() > 0 ? label + " / " + version : label;
    }

    private static Map<String, Object> chooseWorkspace() throws Exception {
        requireContext();
        List<Object> raw = api.listWorkspaces();
        List<WorkspaceChoice> choices = new ArrayList<WorkspaceChoice>();
        for (Object item : raw) {
            choices.add(new WorkspaceChoice(MiniJson.object(item)));
        }
        choices.add(new WorkspaceChoice(null));
        Object selected = NexusDialogs.choose(
            "Select the local CAD workspace used by this Creo session:",
            "Nexus CAD Workspace",
            choices.toArray(),
            choices.get(0)
        );
        if (!(selected instanceof WorkspaceChoice)) {
            return selectedWorkspace;
        }
        WorkspaceChoice choice = (WorkspaceChoice) selected;
        if (choice.workspace == null) {
            String name = NexusDialogs.input(
                "Workspace name:", "Create Nexus CAD Workspace"
            );
            if (name == null || name.trim().length() == 0) {
                return selectedWorkspace;
            }
            selectedWorkspace = api.createWorkspace(name.trim());
        } else {
            selectedWorkspace = choice.workspace;
        }
        session.ChangeDirectory(MiniJson.text(selectedWorkspace, "path"));
        NexusDialogs.info(
            "Creo working directory:\n" + MiniJson.text(selectedWorkspace, "path"),
            "Nexus CAD Workspace"
        );
        return selectedWorkspace;
    }

    private static Map<String, Object> requireWorkspace() throws Exception {
        requireContext();
        if (selectedWorkspace == null) {
            chooseWorkspace();
        }
        if (selectedWorkspace == null) {
            throw new IllegalStateException("Select a Nexus CAD workspace first.");
        }
        return selectedWorkspace;
    }

    private static void retrieve() throws Exception {
        Map<String, Object> workspace = requireWorkspace();
        String fileName = NexusDialogs.input(
            "Managed Creo filename (for example housing.prt or machine.asm):",
            "Retrieve from Nexus"
        );
        if (fileName == null || fileName.trim().length() == 0) {
            return;
        }
        Map<String, Object> status = api.resolveCad(fileName.trim());
        requireManaged(status);
        Map<String, Object> result = api.retrieve(
            MiniJson.integer(status, "id"), MiniJson.text(workspace, "id")
        );
        displayManagedResult(result);
        Map<String, Object> refreshed = MiniJson.object(result.get("cad"));
        String mode = MiniJson.bool(refreshed, "can_modify") ? "editable" : "read-only";
        NexusDialogs.info(
            MiniJson.text(refreshed, "file_name") + " was retrieved " + mode + ".",
            "Nexus Retrieve"
        );
    }

    private static void showStatus() throws Exception {
        Map<String, Object> status = resolveCurrentModel();
        String owner = MiniJson.text(status, "checked_out_by_username");
        String ownerLine = owner.length() == 0 ? "" : "\nOwner: " + owner;
        NexusDialogs.info(
            "CAD: " + MiniJson.text(status, "number")
                + "\nFile: " + MiniJson.text(status, "file_name")
                + "\nRevision: " + MiniJson.text(status, "revision") + "." + MiniJson.text(status, "iteration")
                + "\nLifecycle: " + MiniJson.text(status, "lifecycle_state")
                + "\nState: " + MiniJson.text(status, "checkout_state")
                + ownerLine
                + "\nWorkspace: " + MiniJson.text(status, "checkout_workspace_name")
                + "\nEditable here: " + (MiniJson.bool(status, "can_modify") ? "Yes" : "No"),
            "Nexus CAD Status"
        );
    }

    private static void checkout() throws Exception {
        Map<String, Object> workspace = requireWorkspace();
        Model current = requireCurrentModel();
        ensureReloadIsSafe(current);
        Map<String, Object> status = api.resolveCad(current.GetFileName());
        requireManaged(status);
        if (MiniJson.bool(status, "can_modify")) {
            String selectedWorkspaceId = MiniJson.text(workspace, "id");
            String checkoutWorkspaceId = MiniJson.text(status, "checkout_workspace_id");
            if (!selectedWorkspaceId.equalsIgnoreCase(checkoutWorkspaceId)) {
                throw new IllegalStateException(
                    "This CAD Document is already checked out in workspace "
                        + MiniJson.text(status, "checkout_workspace_name")
                        + ". Select that CAD workspace before reopening it."
                );
            }
            Map<String, Object> existing = api.retrieve(
                MiniJson.integer(status, "id"), selectedWorkspaceId
            );
            eraseForReload(current);
            displayManagedResult(existing);
            NexusDialogs.info(
                "The existing checkout was reopened from its Nexus CAD workspace.",
                "Nexus Check Out"
            );
            return;
        }
        if ("CHECKED_OUT_BY_OTHER".equals(MiniJson.text(status, "checkout_state"))) {
            throw new IllegalStateException(
                "This CAD Document is checked out by "
                    + MiniJson.text(status, "checked_out_by_username") + "."
            );
        }
        boolean reviseReleased = false;
        if ("RELEASED".equals(MiniJson.text(status, "lifecycle_state"))) {
            boolean confirmed = NexusDialogs.confirm(
                "This CAD Document is Released. Create its next CAD revision and check it out?",
                "Nexus Check Out",
                JOptionPane.QUESTION_MESSAGE
            );
            if (!confirmed) return;
            reviseReleased = true;
        }

        Map<String, Object> result = null;
        String itemRevision = null;
        boolean itemRevisionPrompted = false;
        while (result == null) {
            try {
                result = api.checkout(
                    MiniJson.integer(status, "id"),
                    MiniJson.text(workspace, "id"),
                    reviseReleased,
                    itemRevision
                );
            } catch (NexusApiException error) {
                if ("cad_revision_required".equals(error.getCode()) && !reviseReleased) {
                    boolean confirmed = NexusDialogs.confirm(
                        error.getMessage() + "\n\nContinue and revise the affected CAD Documents?",
                        "Nexus CAD Revision",
                        JOptionPane.QUESTION_MESSAGE
                    );
                    if (!confirmed) return;
                    reviseReleased = true;
                    continue;
                }
                if ("item_revision_required".equals(error.getCode()) && !itemRevisionPrompted) {
                    itemRevisionPrompted = true;
                    String revision = NexusDialogs.input(
                        "An associated Item is Released. Enter its next revision (for example B):",
                        "Nexus Item Revision"
                    );
                    if (revision == null || revision.trim().length() == 0) return;
                    itemRevision = revision.trim();
                    continue;
                }
                throw error;
            }
        }

        eraseForReload(current);
        displayManagedResult(result);
        NexusDialogs.info(
            "CAD checkout completed. The managed workspace copy is now editable.",
            "Nexus Check Out"
        );
    }

    private static void checkin() throws Exception {
        Model current = requireCurrentModel();
        Map<String, Object> status = api.resolveCad(current.GetFileName());
        requireManaged(status);
        if (!MiniJson.bool(status, "can_checkin")) {
            throw new IllegalStateException(
                MiniJson.text(status, "read_only_reason").length() == 0
                    ? "This CAD Document is not checked out here."
                    : MiniJson.text(status, "read_only_reason")
            );
        }
        String note = NexusDialogs.input(
            "Check-in comment:", "Nexus Check In"
        );
        if (note == null || note.trim().length() == 0) return;
        if (current.GetIsModified()) {
            current.Save();
        }
        Map<String, Object> result = api.checkin(
            MiniJson.integer(status, "id"),
            MiniJson.text(status, "checkout_workspace_id"),
            "",
            note.trim()
        );
        Map<String, Object> refreshed = MiniJson.object(result.get("cad"));
        NexusDialogs.info(
            "Check-in completed.\nCAD revision: " + MiniJson.text(refreshed, "revision")
                + "." + MiniJson.text(refreshed, "iteration")
                + "\nThe local file is now read-only.",
            "Nexus Check In"
        );
    }

    private static void undoCheckout() throws Exception {
        Model current = requireCurrentModel();
        Map<String, Object> status = api.resolveCad(current.GetFileName());
        requireManaged(status);
        if (!MiniJson.bool(status, "can_modify")) {
            throw new IllegalStateException("The current user does not own this CAD checkout.");
        }
        boolean confirmed = NexusDialogs.confirm(
            "Undo the Nexus checkout? Unsaved in-session changes will be discarded.\n"
                + "Local files will be retained read-only.",
            "Nexus Undo Checkout",
            JOptionPane.WARNING_MESSAGE
        );
        if (!confirmed) return;
        api.undoCheckout(MiniJson.integer(status, "id"), "Undo checkout from Creo");
        try {
            current.EraseWithDependencies();
        } catch (Throwable ignored) {
            try { current.Erase(); } catch (Throwable ignoredAgain) { }
        }
        NexusDialogs.info(
            "Checkout was undone. The retained workspace files are read-only.",
            "Nexus Undo Checkout"
        );
    }

    private static Map<String, Object> resolveCurrentModel() throws Exception {
        Model model = requireCurrentModel();
        Map<String, Object> status = api.resolveCad(model.GetFileName());
        requireManaged(status);
        return status;
    }

    private static Model requireCurrentModel() throws Exception {
        Model model = session.GetCurrentModel();
        if (model == null) {
            throw new IllegalStateException("Open or retrieve a Creo model first.");
        }
        return model;
    }

    private static void eraseForReload(Model model) {
        try {
            model.EraseWithDependencies();
        } catch (Throwable ignored) {
            try { model.Erase(); } catch (Throwable ignoredAgain) { }
        }
    }

    private static void ensureReloadIsSafe(Model root) throws Exception {
        Set<String> modified = new LinkedHashSet<String>();
        if (root.GetIsModified()) {
            modified.add(root.GetFileName());
        }
        Dependencies dependencies = root.ListDependencies();
        if (dependencies != null) {
            for (int index = 0; index < dependencies.getarraysize(); index++) {
                Dependency dependency = dependencies.get(index);
                if (dependency == null || dependency.GetDepModel() == null) continue;
                Model loaded = session.GetModelFromDescr(dependency.GetDepModel());
                if (loaded != null && loaded.GetIsModified()) {
                    modified.add(loaded.GetFileName());
                }
            }
        }
        if (!modified.isEmpty()) {
            throw new IllegalStateException(
                "Save or discard these modified models before checkout reloads the assembly:\n"
                    + join(modified, "\n")
            );
        }
    }

    private static String join(Set<String> values, String separator) {
        StringBuilder result = new StringBuilder();
        for (String value : values) {
            if (result.length() > 0) result.append(separator);
            result.append(value);
        }
        return result.toString();
    }

    private static void requireManaged(Map<String, Object> status) {
        if (!MiniJson.bool(status, "managed")) {
            throw new IllegalStateException(
                "The current Creo file is not registered as a CAD Document in the active Nexus project."
            );
        }
    }

    private static Model displayManagedResult(Map<String, Object> result) throws Exception {
        Map<String, Object> workspace = MiniJson.object(result.get("workspace"));
        String workspacePath = MiniJson.text(workspace, "path");
        String rootPath = MiniJson.text(result, "root_path");
        if (workspacePath.length() == 0 || rootPath.length() == 0) {
            throw new IllegalStateException("Nexus did not return a managed Creo file path.");
        }
        selectedWorkspace = workspace;
        session.ChangeDirectory(workspacePath);
        String logicalFileName = logicalCreoFileName(new File(rootPath).getName());
        ModelDescriptor descriptor = pfcModel.ModelDescriptor_CreateFromFileName(
            logicalFileName
        );
        Model model = session.RetrieveModel(descriptor);
        if (model == null) {
            throw new IllegalStateException("Creo could not retrieve " + rootPath + ".");
        }
        String origin = model.GetOrigin();
        if (origin != null && new File(origin).isAbsolute()) {
            String expectedRoot = new File(workspacePath).getCanonicalPath();
            String actualOrigin = new File(origin).getCanonicalPath();
            if (!(actualOrigin.equalsIgnoreCase(expectedRoot)
                || actualOrigin.toLowerCase().startsWith(
                    (expectedRoot + File.separator).toLowerCase()
                ))) {
                throw new IllegalStateException(
                    "Creo retained another in-session copy of " + model.GetFileName()
                        + ". Erase that model, then retrieve it again from Nexus."
                );
            }
        }
        model.Display();
        return model;
    }

    private static String logicalCreoFileName(String fileName) {
        String name = fileName == null ? "" : new File(fileName).getName();
        return name.replaceFirst("(?i)\\.(prt|asm|drw)\\.\\d+$", ".$1");
    }

    private static void showError(Throwable error) {
        String message = error == null ? "Unknown error" : error.getMessage();
        if (message == null || message.trim().length() == 0) {
            message = String.valueOf(error);
        }
        if (error instanceof NexusApiException) {
            String code = ((NexusApiException) error).getCode();
            if (code.length() > 0) message = message + "\n\nCode: " + code;
        }
        NexusDialogs.error(message, "Nexus PDM");
    }

    private interface CommandAction {
        void run() throws Exception;
    }

    private static class CommandGuardRegistration {
        final UICommand command;
        final NexusSaveGuard listener;

        CommandGuardRegistration(UICommand command, NexusSaveGuard listener) {
            this.command = command;
            this.listener = listener;
        }
    }

    private static final class CommandListener extends DefaultUICommandActionListener {
        private final CommandAction action;

        private CommandListener(CommandAction action) {
            this.action = action;
        }

        public void OnCommand() {
            try {
                action.run();
            } catch (Throwable error) {
                showError(error);
            }
        }
    }

    private static final class WorkspaceChoice {
        private final Map<String, Object> workspace;

        private WorkspaceChoice(Map<String, Object> workspace) {
            this.workspace = workspace;
        }

        public String toString() {
            if (workspace == null) return "<Create new workspace...>";
            String name = MiniJson.text(workspace, "name");
            String path = MiniJson.text(workspace, "path");
            return name + (path.length() == 0 ? "" : "  [" + path + "]");
        }
    }
}
