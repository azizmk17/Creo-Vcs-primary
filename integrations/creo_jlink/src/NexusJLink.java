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
        addCommand("NexusPDM.WorkspaceStatus", "NexusWorkspaceStatus", "NexusWorkspaceStatusHelp", new CommandAction() {
            public void run() throws Exception { showWorkspaceStatus(); }
        });
        addCommand("NexusPDM.History", "NexusHistory", "NexusHistoryHelp", new CommandAction() {
            public void run() throws Exception { showHistory(); }
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
        addCommand("NexusPDM.Revise", "NexusRevise", "NexusReviseHelp", new CommandAction() {
            public void run() throws Exception { reviseCurrent(); }
        });
        addCommand("NexusPDM.Release", "NexusRelease", "NexusReleaseHelp", new CommandAction() {
            public void run() throws Exception { releaseCurrent(); }
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
        Map<String, Object> status = chooseProjectCadDocument("Retrieve from Nexus");
        if (status == null) {
            return;
        }
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

    private static Map<String, Object> chooseProjectCadDocument(String title) throws Exception {
        List<Object> raw;
        try {
            raw = api.listCadDocuments();
        } catch (NexusApiException error) {
            if ("route_not_found".equals(error.getCode())) {
                throw new IllegalStateException(
                    "The running Nexus bridge does not include the CAD Document list route yet.\n\n"
                        + "Close and restart the Nexus desktop app, then reconnect Creo to the project."
                );
            }
            throw error;
        }
        List<CadChoice> choices = new ArrayList<CadChoice>();
        for (Object item : raw) {
            Map<String, Object> cad = MiniJson.object(item);
            if (MiniJson.bool(cad, "managed")) {
                choices.add(new CadChoice(cad));
            }
        }
        if (choices.isEmpty()) {
            throw new IllegalStateException(
                "The active Nexus project has no managed CAD Documents to retrieve."
            );
        }
        Object selected = NexusDialogs.choose(
            "Select a CAD Document from the active Nexus project:",
            title,
            choices.toArray(),
            choices.get(0)
        );
        if (!(selected instanceof CadChoice)) {
            return null;
        }
        return ((CadChoice) selected).cad;
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

    private static void showWorkspaceStatus() throws Exception {
        Map<String, Object> workspace = requireWorkspace();
        Map<String, Object> state = loadWorkspaceState(MiniJson.text(workspace, "id"));
        List<Object> localFiles = MiniJson.array(state.get("local_files"));
        List<Object> checkouts = MiniJson.array(state.get("cad_documents"));
        List<String> lines = new ArrayList<String>();
        lines.add("Workspace: " + MiniJson.text(workspace, "name"));
        lines.add("Path: " + MiniJson.text(workspace, "path"));
        lines.add("Local Creo files: " + localFiles.size());
        lines.add("Active Nexus checkouts: " + checkouts.size());
        lines.add("");
        for (Object raw : localFiles) {
            Map<String, Object> file = MiniJson.object(raw);
            lines.add(MiniJson.text(file, "filename") + " - "
                + MiniJson.text(file, "status") + " - "
                + MiniJson.text(file, "detail"));
        }
        NexusDialogs.info(joinList(lines, "\n"), "Nexus Workspace Status");
    }

    private static void showHistory() throws Exception {
        Map<String, Object> status = resolveCurrentModel();
        Map<String, Object> result = api.cadHistory(MiniJson.integer(status, "id"));
        List<Object> history = MiniJson.array(result.get("history"));
        List<String> lines = new ArrayList<String>();
        lines.add("CAD: " + MiniJson.text(status, "file_name"));
        lines.add("Current revision: " + MiniJson.text(status, "revision") + "."
            + MiniJson.integer(status, "iteration"));
        lines.add("");
        for (Object raw : history) {
            Map<String, Object> entry = MiniJson.object(raw);
            String action = MiniJson.text(entry, "action");
            String timestamp = MiniJson.text(entry, "created_at");
            String user = MiniJson.text(entry, "username");
            if (user.length() == 0) user = MiniJson.text(entry, "user_id");
            String note = MiniJson.text(entry, "note");
            String workspaceName = MiniJson.text(entry, "workspace_name");
            String line = timestamp + "  " + action;
            if (user.length() > 0) line += "  by " + user;
            if (workspaceName.length() > 0) line += "  [" + workspaceName + "]";
            if (note.length() > 0) line += "\n  " + note;
            lines.add(line);
        }
        if (history.isEmpty()) lines.add("No checkout history is recorded.");
        NexusDialogs.info(joinList(lines, "\n"), "Nexus CAD History");
    }

    private static void reviseCurrent() throws Exception {
        Map<String, Object> status = resolveCurrentModel();
        if (MiniJson.bool(status, "can_modify")) {
            throw new IllegalStateException(
                "Undo or check in the active checkout before creating a new CAD revision."
            );
        }
        boolean confirmed = NexusDialogs.confirm(
            "Create the next CAD revision for " + MiniJson.text(status, "file_name") + "?",
            "Nexus CAD Revision",
            JOptionPane.QUESTION_MESSAGE
        );
        if (!confirmed) return;
        Map<String, Object> result = api.revise(MiniJson.integer(status, "id"));
        Map<String, Object> revised = MiniJson.object(result.get("cad"));
        NexusDialogs.info(
            "New CAD revision created: " + MiniJson.text(revised, "revision") + "."
                + MiniJson.integer(revised, "iteration")
                + "\nCheck it out before editing.",
            "Nexus CAD Revision"
        );
    }

    private static void releaseCurrent() throws Exception {
        Map<String, Object> status = resolveCurrentModel();
        if (MiniJson.text(status, "checkout_state").length() > 0
            && !"CHECKED_IN".equals(MiniJson.text(status, "checkout_state"))) {
            throw new IllegalStateException(
                "Check in or undo the active checkout before releasing this CAD Document."
            );
        }
        boolean confirmed = NexusDialogs.confirm(
            "Release " + MiniJson.text(status, "file_name") + " at revision "
                + MiniJson.text(status, "revision") + "." + MiniJson.integer(status, "iteration") + "?",
            "Nexus Release CAD",
            JOptionPane.QUESTION_MESSAGE
        );
        if (!confirmed) return;
        Map<String, Object> result = api.release(MiniJson.integer(status, "id"));
        Map<String, Object> released = MiniJson.object(result.get("cad"));
        NexusDialogs.info(
            "CAD Document released at revision " + MiniJson.text(released, "revision")
                + "." + MiniJson.integer(released, "iteration") + ".",
            "Nexus Release CAD"
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
        Map<String, Object> workspace = requireWorkspace();
        List<CheckinChoice> choices = checkinChoices(workspace);
        if (choices.isEmpty()) {
            NexusDialogs.info(
                "No managed CAD Documents are available for check-in in this workspace.",
                "Nexus Check In"
            );
            return;
        }
        Object[] labels = new Object[choices.size()];
        boolean[] selected = new boolean[choices.size()];
        for (int index = 0; index < choices.size(); index++) {
            CheckinChoice choice = choices.get(index);
            labels[index] = choice;
            selected[index] = choice.defaultSelected();
        }
        NexusDialogs.ChecklistResult selection = NexusDialogs.checklist(
            "Select the CAD Documents to check in from this workspace:",
            "Nexus Check In",
            labels,
            selected,
            "Check-in comment:"
        );
        if (selection == null) return;
        String note = selection.note.trim();
        if (note.length() == 0) return;
        if (selection.selectedIndexes.length == 0) {
            NexusDialogs.info("No CAD Documents were selected.", "Nexus Check In");
            return;
        }

        List<String> checkedIn = new ArrayList<String>();
        for (int index = 0; index < selection.selectedIndexes.length; index++) {
            CheckinChoice choice = choices.get(selection.selectedIndexes[index]);
            if (!MiniJson.bool(choice.status, "can_checkin")) {
                String reason = MiniJson.text(choice.status, "read_only_reason");
                throw new IllegalStateException(
                    choice.fileName() + " cannot be checked in."
                        + (reason.length() == 0 ? "" : "\n" + reason)
                );
            }
            if (choice.loaded != null && choice.loaded.model.GetIsModified()) {
                choice.loaded.model.Save();
            }
            Map<String, Object> result = api.checkin(
                MiniJson.integer(choice.status, "id"),
                MiniJson.text(choice.status, "checkout_workspace_id"),
                "",
                note
            );
            Map<String, Object> refreshed = MiniJson.object(result.get("cad"));
            checkedIn.add(
                MiniJson.text(refreshed, "file_name")
                    + " Rev " + MiniJson.text(refreshed, "revision")
                    + "." + MiniJson.text(refreshed, "iteration")
            );
        }
        NexusDialogs.info(
            "Check-in completed:\n" + joinList(checkedIn, "\n")
                + "\n\nThe checked-in local files are now read-only.",
            "Nexus Check In"
        );
    }

    private static List<CheckinChoice> checkinChoices(Map<String, Object> workspace)
        throws Exception {
        List<CheckinChoice> choices = new ArrayList<CheckinChoice>();
        Map<Integer, CheckinChoice> byId = new LinkedHashMap<Integer, CheckinChoice>();
        Map<String, Object> workspaceState = loadWorkspaceState(MiniJson.text(workspace, "id"));
        List<Object> localRows = MiniJson.array(workspaceState.get("local_files"));
        for (Object item : localRows) {
            Map<String, Object> local = MiniJson.object(item);
            int cadId = MiniJson.integer(local, "cad_document_id");
            Map<String, Object> status = MiniJson.object(item);
            if (cadId > 0) {
                try {
                    status = api.cadStatus(cadId);
                } catch (Exception ignored) {
                    status = new LinkedHashMap<String, Object>();
                }
            }
            CheckinChoice choice = new CheckinChoice(status);
            choice.localFile = local;
            choices.add(choice);
            if (cadId > 0) {
                byId.put(Integer.valueOf(cadId), choice);
            }
        }

        List<Object> workspaceRows = MiniJson.array(workspaceState.get("cad_documents"));
        for (Object item : workspaceRows) {
            Map<String, Object> status = MiniJson.object(item);
            if (!MiniJson.bool(status, "managed")) continue;
            Integer id = Integer.valueOf(MiniJson.integer(status, "id"));
            CheckinChoice choice = byId.get(id);
            if (choice == null) {
                choice = new CheckinChoice(status);
                choices.add(choice);
                byId.put(id, choice);
            } else {
                choice.status = status;
            }
            choice.workspaceCheckout = true;
        }

        Map<String, LoadedModelInfo> loaded = loadedCreoModels();
        for (LoadedModelInfo info : loaded.values()) {
            Map<String, Object> status;
            try {
                status = api.resolveCad(info.model.GetFileName());
            } catch (Exception ignored) {
                continue;
            }
            if (!MiniJson.bool(status, "managed")) continue;
            Integer id = Integer.valueOf(MiniJson.integer(status, "id"));
            CheckinChoice choice = byId.get(id);
            if (choice == null) {
                choice = new CheckinChoice(status);
                choices.add(choice);
                byId.put(id, choice);
            } else {
                choice.status = status;
            }
            choice.loaded = info;
        }
        return choices;
    }

    private static Map<String, Object> loadWorkspaceState(String workspaceId)
        throws Exception {
        try {
            return api.listWorkspaceState(workspaceId);
        } catch (NexusApiException error) {
            if ("route_not_found".equals(error.getCode())) {
                throw new IllegalStateException(
                    "The running Nexus bridge does not include workspace status yet.\n\n"
                        + "Close and restart the Nexus desktop app, then reconnect Creo to the project."
                );
            }
            throw error;
        }
    }

    private static Map<String, LoadedModelInfo> loadedCreoModels() {
        Map<String, LoadedModelInfo> loaded = new LinkedHashMap<String, LoadedModelInfo>();
        Model current = null;
        try {
            current = session.GetCurrentModel();
        } catch (Throwable ignored) {
        }
        if (current == null) {
            return loaded;
        }
        walkLoadedModels(loaded, current, "current", new LinkedHashSet<String>());
        return loaded;
    }

    private static void walkLoadedModels(
        Map<String, LoadedModelInfo> loaded,
        Model model,
        String source,
        Set<String> visiting
    ) {
        if (model == null) return;
        String key = "";
        try { key = logicalCreoFileName(model.GetFileName()).toLowerCase(); }
        catch (Throwable ignored) { }
        if (key.length() == 0 || !visiting.add(key)) return;
        addLoadedModel(loaded, model, source);
        try {
            Dependencies dependencies = model.ListDependencies();
            if (dependencies != null) {
                for (int index = 0; index < dependencies.getarraysize(); index++) {
                    Dependency dependency = dependencies.get(index);
                    if (dependency == null || dependency.GetDepModel() == null) continue;
                    Model child = session.GetModelFromDescr(dependency.GetDepModel());
                    if (child != null) {
                        walkLoadedModels(loaded, child, "loaded child", visiting);
                    }
                }
            }
        } catch (Throwable ignored) {
        } finally {
            visiting.remove(key);
        }
    }

    private static void addLoadedModel(
        Map<String, LoadedModelInfo> loaded,
        Model model,
        String source
    ) {
        try {
            String key = logicalCreoFileName(model.GetFileName()).toLowerCase();
            if (key.length() > 0 && !loaded.containsKey(key)) {
                loaded.put(key, new LoadedModelInfo(model, source));
            }
        } catch (Throwable ignored) {
        }
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
        for (LoadedModelInfo loaded : loadedCreoModels().values()) {
            if (loaded.model != root && loaded.modified()) {
                modified.add(loaded.model.GetFileName());
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

    private static String joinList(List<String> values, String separator) {
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

    private static final class CadChoice {
        private final Map<String, Object> cad;

        private CadChoice(Map<String, Object> cad) {
            this.cad = cad;
        }

        public String toString() {
            String fileName = MiniJson.text(cad, "file_name");
            String number = MiniJson.text(cad, "number");
            String revision = MiniJson.text(cad, "revision");
            String iteration = String.valueOf(MiniJson.integer(cad, "iteration"));
            String category = MiniJson.text(cad, "category");
            String state = MiniJson.text(cad, "checkout_state");
            String label = fileName.length() > 0 ? fileName : number;
            if (number.length() > 0 && !number.equals(label)) {
                label = label + " - " + number;
            }
            if (revision.length() > 0 || iteration.length() > 0) {
                label = label + "  Rev " + revision + "." + iteration;
            }
            if (category.length() > 0) {
                label = label + "  [" + category + "]";
            }
            if (state.length() > 0) {
                label = label + "  " + state;
            }
            return label;
        }
    }

    private static final class LoadedModelInfo {
        private final Model model;
        private final String source;

        private LoadedModelInfo(Model model, String source) {
            this.model = model;
            this.source = source;
        }

        private boolean modified() {
            try {
                return model.GetIsModified();
            } catch (Throwable ignored) {
                return false;
            }
        }
    }

    private static final class CheckinChoice {
        private Map<String, Object> status;
        private Map<String, Object> localFile;
        private boolean workspaceCheckout;
        private LoadedModelInfo loaded;

        private CheckinChoice(Map<String, Object> status) {
            this.status = status;
        }

        private String fileName() {
            String name = MiniJson.text(status, "file_name");
            if (name.length() == 0 && localFile != null) {
                name = MiniJson.text(localFile, "logical_file_name");
            }
            if (name.length() == 0 && localFile != null) {
                name = MiniJson.text(localFile, "filename");
            }
            return name;
        }

        private boolean defaultSelected() {
            return MiniJson.bool(status, "can_checkin")
                && (
                    (loaded != null && ("current".equals(loaded.source) || loaded.modified()))
                    || (localFile != null && MiniJson.bool(localFile, "selectable"))
                );
        }

        public String toString() {
            String label = fileName();
            if (label.length() == 0) {
                label = MiniJson.text(status, "number");
            }
            String revision = MiniJson.text(status, "revision");
            int iteration = MiniJson.integer(status, "iteration");
            if (revision.length() > 0 || iteration > 0) {
                label = label + "  Rev " + revision + "." + iteration;
            }
            List<String> tags = new ArrayList<String>();
            if (localFile != null) tags.add("local workspace");
            if (workspaceCheckout) tags.add("workspace checkout");
            if (loaded != null) {
                tags.add(loaded.source);
                if (loaded.modified()) tags.add("modified");
            } else {
                tags.add("not loaded");
            }
            if (localFile != null) {
                String statusText = MiniJson.text(localFile, "status");
                if (statusText.length() > 0) tags.add(statusText);
            }
            if (!MiniJson.bool(status, "can_checkin")) tags.add("read-only");
            label = label + "  [" + joinList(tags, ", ") + "]";
            String reason = MiniJson.text(status, "read_only_reason");
            if (reason.length() == 0 && localFile != null) {
                reason = MiniJson.text(localFile, "detail");
            }
            if (!MiniJson.bool(status, "can_checkin") && reason.length() > 0) {
                label = label + " - " + reason;
            }
            return label;
        }
    }
}
