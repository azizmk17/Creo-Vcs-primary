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
import com.ptc.pfc.pfcCommand.UICommandBracketListener;
import com.ptc.pfc.pfcGlobal.pfcGlobal;
import com.ptc.pfc.pfcModel.Dependencies;
import com.ptc.pfc.pfcModel.Dependency;
import com.ptc.pfc.pfcModel.Model;
import com.ptc.pfc.pfcModel.ModelDescriptor;
import com.ptc.pfc.pfcModel.pfcModel;
import com.ptc.pfc.pfcSession.Session;
import com.ptc.pfc.pfcWindow.Window;

public class NexusJLink {
    private static Session session;
    private static NexusApiClient api;
    private static final List<CommandGuardRegistration> commandGuards =
        new ArrayList<CommandGuardRegistration>();
    private static final Set<String> registeredGuardNames =
        new LinkedHashSet<String>();
    private static final Set<String> unauthorizedModificationAlerts =
        new LinkedHashSet<String>();
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
        registeredGuardNames.clear();
        unauthorizedModificationAlerts.clear();
        selectedWorkspace = null;
        api = null;
        session = null;
    }

    private static void installCommandGuards() throws jxthrowable {
        addCommandGuard("ProCmdModelSave", "save");
        addCommandGuard("ProCmdModelRename", "rename");
        String[] editCommands = new String[] {
            "ProCmdModelEdit",
            "ProCmdModelModify",
            "ProCmdFeatEdit",
            "ProCmdFeatEditDef",
            "ProCmdFeatRedefine",
            "ProCmdFeatDelete",
            "ProCmdEditDelete",
            "ProCmdFeatSuppress",
            "ProCmdFeatResume",
            "ProCmdDdim",
            "ProCmdEditDim",
            // Creo 3.0 commands captured from the actual ribbon/tree workflows.
            "ProCmdDynEdit",
            "ProCmdEditValueDim",
            "ProCmdL05Edit",
            "ProCmdL05Edit@PopupMenuTree",
            "ProCmdL05EditFeat",
            "ProCmdRedefine",
            "ProCmdRedefine@PopupMenuTree",
            "ProCmdEditProperties",
            "ProCmdEditProperties@PopupMenuTree",
            "ProCmdEditOneByOne",
            "ProCmdFtExtrude",
            "ProCmdFtHole",
            "ProCmdFtRevolve",
            "ProCmdFtSweep",
            "ProCmdFtBlend",
            "ProCmdFtRound",
            "ProCmdFtChamfer",
            "ProCmdFtDraft",
            "ProCmdFtPattern",
            "ProCmdFtMirror",
            "ProCmdFtShell",
            "ProCmdFtRib",
            "ProCmdCompAssem",
            "ProCmdSuppressFeat",
            "ProCmdRegenPart"
        };
        for (String commandName : editCommands) {
            addEditCommandGuard(commandName);
        }
    }

    private static void addCommandGuard(String commandName, String action) throws jxthrowable {
        if (registeredGuardNames.contains(commandName)) {
            return;
        }
        UICommand command = session.UIGetCommand(commandName);
        if (command == null) {
            System.out.println("Nexus PDM save guard unavailable for Creo command: " + commandName);
            return;
        }
        NexusSaveGuard listener = new NexusSaveGuard(session, api, action);
        command.AddActionListener(listener);
        commandGuards.add(new CommandGuardRegistration(command, listener));
        registeredGuardNames.add(commandName);
        System.out.println("Nexus PDM save guard registered: " + commandName);
    }

    private static void addEditCommandGuard(String commandName) throws jxthrowable {
        if (registeredGuardNames.contains(commandName)) {
            return;
        }
        UICommand command = session.UIGetCommand(commandName);
        if (command == null) {
            System.out.println("Nexus PDM edit guard unavailable for Creo command: " + commandName);
            return;
        }
        NexusEditGuard listener = new NexusEditGuard();
        command.AddActionListener(listener);
        commandGuards.add(new CommandGuardRegistration(command, listener));
        registeredGuardNames.add(commandName);
        System.out.println("Nexus PDM edit guard registered: " + commandName);
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
        installCommandGuards();
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

    static Map<String, Object> resolveCadForModel(Model model) throws Exception {
        if (api == null || model == null) {
            return new LinkedHashMap<String, Object>();
        }
        String workspaceId = selectedWorkspace == null
            ? ""
            : MiniJson.text(selectedWorkspace, "id");
        return api.resolveCad(model.GetFileName(), workspaceId);
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
        installCommandGuards();
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
        installCommandGuards();
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

    /** Called immediately before Creo starts a feature, dimension, or delete edit. */
    static boolean allowModelEdit() throws Exception {
        Model current = currentOrActiveModel();
        if (current == null || api == null) return true;
        Map<String, Object> status = resolveCadForModel(current);
        if (!MiniJson.bool(status, "managed")
            || MiniJson.bool(status, "can_modify")
            || MiniJson.bool(status, "local_edit_intent")) {
            return true;
        }

        String owner = MiniJson.text(status, "checked_out_by_username");
        if (owner.length() == 0 && "CHECKED_OUT_BY_OTHER".equals(
            MiniJson.text(status, "checkout_state")
        )) {
            owner = "another Nexus user";
        }
        String ownerLine = owner.length() == 0 ? "" : "\nOwner: " + owner;
        int decision = NexusDialogs.editChoice(
            "This CAD Document is read-only in Nexus." + ownerLine
                + "\n\nCheck Out Now: obtain the Nexus lock and continue the edit."
                + "\nContinue Locally: allow local editing, but server check-in remains blocked.",
            "Nexus Edit Conflict"
        );
        if (decision == 0) {
            Map<String, Object> workspace = requireWorkspace();
            boolean retained = checkoutCurrentModel(status, current, workspace, false, false);
            if (!retained) {
                NexusDialogs.info(
                    "The checked-out workspace copy is now open. Retry the edit command on that model.",
                    "Nexus Edit Conflict"
                );
            }
            return retained;
        }
        if (decision == 1) {
            Map<String, Object> workspace = requireWorkspace();
            if (!modelMatchesWorkspace(
                current,
                MiniJson.text(workspace, "path"),
                MiniJson.text(status, "file_name")
            )) {
                throw new IllegalStateException(
                    "Open the CAD Document from its selected Nexus workspace before continuing locally."
                );
            }
            api.setEditIntent(
                MiniJson.integer(status, "id"),
                MiniJson.text(workspace, "id"),
                "User chose to continue locally."
            );
            return true;
        }
        return false;
    }

    /** Catch feature-edit paths that Creo reports as modified only after the command runs. */
    static void checkForUnauthorizedModifications() throws Exception {
        if (api == null) return;
        for (LoadedModelInfo info : loadedCreoModels().values()) {
            String key = logicalCreoFileName(safeFileName(info.model)).toLowerCase();
            if (key.length() == 0) continue;
            if (!info.modified()) {
                unauthorizedModificationAlerts.remove(key);
                continue;
            }
            Map<String, Object> status;
            try {
                status = resolveCadForModel(info.model);
            } catch (Exception ignored) {
                continue;
            }
            if (!MiniJson.bool(status, "managed")
                || MiniJson.bool(status, "can_modify")
                || MiniJson.bool(status, "local_edit_intent")
                || unauthorizedModificationAlerts.contains(key)) {
                continue;
            }
            unauthorizedModificationAlerts.add(key);
            try {
                handleDetectedUnauthorizedModification(status, info.model);
            } catch (Throwable error) {
                unauthorizedModificationAlerts.remove(key);
                if (error instanceof Exception) throw (Exception) error;
                throw new IllegalStateException(error.getMessage(), error);
            }
        }
    }

    private static void handleDetectedUnauthorizedModification(
        Map<String, Object> status, Model model
    ) throws Exception {
        String owner = MiniJson.text(status, "checked_out_by_username");
        if (owner.length() == 0 && "CHECKED_OUT_BY_OTHER".equals(
            MiniJson.text(status, "checkout_state")
        )) {
            owner = "another Nexus user";
        }
        String ownerLine = owner.length() == 0 ? "" : "\nOwner: " + owner;
        int decision = NexusDialogs.editChoice(
            "Creo marked this managed CAD Document as modified, but Nexus does not "
                + "allow this workspace to modify it." + ownerLine
                + "\n\nCheck Out Now: keep the local change and obtain the Nexus lock."
                + "\nContinue Locally: keep the local change, but block server Save/check-in."
                + "\nCancel: leave the model read-only to Nexus.",
            "Nexus Modification Conflict"
        );
        if (decision == 0) {
            Map<String, Object> workspace = requireWorkspace();
            checkoutCurrentModel(status, model, workspace, true, false);
            return;
        }
        if (decision == 1) {
            Map<String, Object> workspace = requireWorkspace();
            if (!modelMatchesWorkspace(
                model,
                MiniJson.text(workspace, "path"),
                MiniJson.text(status, "file_name")
            )) {
                throw new IllegalStateException(
                    "Open the CAD Document from its selected Nexus workspace before continuing locally."
                );
            }
            api.setEditIntent(
                MiniJson.integer(status, "id"),
                MiniJson.text(workspace, "id"),
                "Creo detected a local modification before checkout."
            );
            return;
        }
        NexusDialogs.warning(
            "Nexus detected a modification to a CAD Document that is not checked out.\n\n"
                + "Nexus Save and check-in are blocked. Undo the Creo change or check out "
                + "the CAD Document before continuing.",
            "Nexus Modification Blocked"
        );
    }

    private static void checkout() throws Exception {
        Map<String, Object> workspace = requireWorkspace();
        installCommandGuards();
        Model current = requireCurrentModel();
        Map<String, Object> status = resolveCadForModel(current);
        requireManaged(status);
        boolean currentIsWorkspaceModel = modelMatchesWorkspace(
            current,
            MiniJson.text(workspace, "path"),
            MiniJson.text(status, "file_name")
        );
        if (MiniJson.bool(status, "can_modify")) {
            String checkoutWorkspaceId = MiniJson.text(status, "checkout_workspace_id");
            if (!MiniJson.text(workspace, "id").equalsIgnoreCase(checkoutWorkspaceId)) {
                throw new IllegalStateException(
                    "This CAD Document is already checked out in workspace "
                        + MiniJson.text(status, "checkout_workspace_name")
                        + ". Select that CAD workspace before reopening it."
                );
            }
            if (currentIsWorkspaceModel) {
                activateModel(current);
            } else {
                ensureReloadIsSafe(current, workspace, false);
                Map<String, Object> existing = api.retrieve(
                    MiniJson.integer(status, "id"), MiniJson.text(workspace, "id")
                );
                eraseForReload(current);
                displayManagedResult(existing);
            }
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
        boolean preserveLocalChanges = MiniJson.bool(status, "local_edit_intent");
        if (preserveLocalChanges) {
            boolean adopt = NexusDialogs.confirm(
                "A local edit intent is active. Adopt the local changes into the Nexus checkout?",
                "Nexus Check Out",
                JOptionPane.QUESTION_MESSAGE
            );
            if (!adopt) return;
        }
        ensureReloadIsSafe(current, workspace, preserveLocalChanges);
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
                    itemRevision,
                    preserveLocalChanges
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

        if (currentIsWorkspaceModel) {
            activateModel(current);
        } else {
            eraseForReload(current);
            displayManagedResult(result);
        }
        NexusDialogs.info(
            "CAD checkout completed. The managed workspace copy is now editable.",
            "Nexus Check Out"
        );
    }

    private static boolean checkoutCurrentModel(
        Map<String, Object> status,
        Model current,
        Map<String, Object> workspace,
        boolean preserveLocalChanges,
        boolean notify
    ) throws Exception {
        if ("CHECKED_OUT_BY_OTHER".equals(MiniJson.text(status, "checkout_state"))) {
            throw new IllegalStateException(
                "This CAD Document is checked out by "
                    + (MiniJson.text(status, "checked_out_by_username").length() == 0
                        ? "another Nexus user"
                        : MiniJson.text(status, "checked_out_by_username")) + "."
            );
        }
        boolean reviseReleased = false;
        if ("RELEASED".equals(MiniJson.text(status, "lifecycle_state"))) {
            boolean confirmed = NexusDialogs.confirm(
                "This CAD Document is Released. Create its next CAD revision and check it out?",
                "Nexus Check Out",
                JOptionPane.QUESTION_MESSAGE
            );
            if (!confirmed) return false;
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
                    itemRevision,
                    preserveLocalChanges
                );
            } catch (NexusApiException error) {
                if ("cad_revision_required".equals(error.getCode()) && !reviseReleased) {
                    boolean confirmed = NexusDialogs.confirm(
                        error.getMessage() + "\n\nContinue and revise the affected CAD Documents?",
                        "Nexus CAD Revision",
                        JOptionPane.QUESTION_MESSAGE
                    );
                    if (!confirmed) return false;
                    reviseReleased = true;
                    continue;
                }
                if ("item_revision_required".equals(error.getCode()) && !itemRevisionPrompted) {
                    itemRevisionPrompted = true;
                    String revision = NexusDialogs.input(
                        "An associated Item is Released. Enter its next revision (for example B):",
                        "Nexus Item Revision"
                    );
                    if (revision == null || revision.trim().length() == 0) return false;
                    itemRevision = revision.trim();
                    continue;
                }
                throw error;
            }
        }
        boolean retained = modelMatchesWorkspace(
            current,
            MiniJson.text(workspace, "path"),
            MiniJson.text(status, "file_name")
        );
        if (retained) {
            activateModel(current);
        } else {
            eraseForReload(current);
            displayManagedResult(result);
        }
        if (notify) {
            NexusDialogs.info(
                "CAD checkout completed. The managed workspace copy is now editable.",
                "Nexus Check Out"
            );
        }
        return retained;
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

        List<Integer> selectedCadIds = new ArrayList<Integer>();
        for (int index = 0; index < selection.selectedIndexes.length; index++) {
            CheckinChoice choice = choices.get(selection.selectedIndexes[index]);
            selectedCadIds.add(Integer.valueOf(MiniJson.integer(choice.status, "id")));
        }
        Map<String, Object> plan = api.checkinPlan(selectedCadIds);
        List<Object> pendingCommits = MiniJson.array(plan.get("pending_commits"));
        String targetCommitId = "";
        if (!pendingCommits.isEmpty()) {
            Object[] pendingOptions = new Object[pendingCommits.size() + 1];
            pendingOptions[0] = "Create a new pending commit";
            for (int index = 0; index < pendingCommits.size(); index++) {
                Map<String, Object> pending = MiniJson.object(pendingCommits.get(index));
                pendingOptions[index + 1] = "Add to: "
                    + MiniJson.text(pending, "title")
                    + " [" + MiniJson.text(pending, "commit_id") + "]";
            }
            Object selectedPending = NexusDialogs.choose(
                "You already have a Pending commit. Add these models to it or create a new commit:",
                "Nexus Pending Commit",
                pendingOptions,
                pendingOptions[1]
            );
            if (selectedPending == null) return;
            for (int index = 1; index < pendingOptions.length; index++) {
                if (pendingOptions[index].equals(selectedPending)) {
                    targetCommitId = MiniJson.text(
                        MiniJson.object(pendingCommits.get(index - 1)), "commit_id"
                    );
                    break;
                }
            }
        }

        Map<Integer, Map<String, Object>> conflicts =
            new LinkedHashMap<Integer, Map<String, Object>>();
        for (Object value : MiniJson.array(plan.get("conflicts"))) {
            Map<String, Object> conflict = MiniJson.object(value);
            conflicts.put(
                Integer.valueOf(MiniJson.integer(conflict, "cad_document_id")), conflict
            );
        }

        List<String> staged = new ArrayList<String>();
        List<String> skipped = new ArrayList<String>();
        for (int index = 0; index < selection.selectedIndexes.length; index++) {
            CheckinChoice choice = choices.get(selection.selectedIndexes[index]);
            if (!MiniJson.bool(choice.status, "can_checkin")) {
                String reason = MiniJson.text(choice.status, "read_only_reason");
                throw new IllegalStateException(
                    choice.fileName() + " cannot be checked in."
                        + (reason.length() == 0 ? "" : "\n" + reason)
                );
            }
            int cadId = MiniJson.integer(choice.status, "id");
            String duplicateAction = "error";
            Map<String, Object> conflict = conflicts.get(Integer.valueOf(cadId));
            if (conflict != null) {
                if (!MiniJson.bool(conflict, "replace_allowed")) {
                    boolean skip = NexusDialogs.confirm(
                        choice.fileName() + " is already pending for another Nexus user."
                            + "\n\nSkip this model and continue with the batch?",
                        "Nexus Pending File",
                        JOptionPane.WARNING_MESSAGE
                    );
                    if (!skip) return;
                    skipped.add(choice.fileName());
                    continue;
                }
                int action = NexusDialogs.replaceChoice(
                    choice.fileName() + " is already in Pending commit \""
                        + MiniJson.text(conflict, "title") + "\".\n\n"
                        + "Replace its pending copy or skip this model?",
                    "Nexus Pending File"
                );
                if (action < 0) return;
                if (action == 1) {
                    skipped.add(choice.fileName());
                    continue;
                }
                duplicateAction = "replace";
            }
            if (choice.loaded != null && choice.loaded.model.GetIsModified()) {
                choice.loaded.model.Save();
            }
            Map<String, Object> result = api.checkin(
                cadId,
                MiniJson.text(choice.status, "checkout_workspace_id"),
                "",
                note,
                targetCommitId,
                duplicateAction
            );
            Map<String, Object> pending = MiniJson.object(result.get("pending_commit"));
            if (targetCommitId.length() == 0) {
                targetCommitId = MiniJson.text(pending, "commit_id");
            }
            staged.add(choice.fileName());
        }
        String skippedText = skipped.isEmpty()
            ? ""
            : "\n\nSkipped:\n" + joinList(skipped, "\n");
        if (staged.isEmpty()) {
            NexusDialogs.info(
                "No models were staged." + skippedText,
                "Nexus Check In"
            );
            return;
        }
        NexusDialogs.info(
            "Models staged in Pending commit " + targetCommitId + ":\n"
                + joinList(staged, "\n") + skippedText
                + "\n\nThe CAD and associated Item checkouts remain active until approval and merge.",
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
                status = resolveCadForModel(info.model);
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
        Model current = currentOrActiveModel();
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
        Map<String, Object> status = resolveCadForModel(current);
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
        installCommandGuards();
        Model model = requireCurrentModel();
        Map<String, Object> status = resolveCadForModel(model);
        requireManaged(status);
        return status;
    }

    private static Model requireCurrentModel() throws Exception {
        Model model = currentOrActiveModel();
        if (model == null) {
            throw new IllegalStateException("Open or retrieve a Creo model first.");
        }
        return model;
    }

    private static Model currentOrActiveModel() {
        try {
            Model current = session.GetCurrentModel();
            if (current != null) return current;
        } catch (Throwable ignored) {
        }
        try {
            return session.GetActiveModel();
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static void eraseForReload(Model model) {
        try {
            model.EraseWithDependencies();
        } catch (Throwable ignored) {
            try { model.Erase(); } catch (Throwable ignoredAgain) { }
        }
    }

    private static void ensureReloadIsSafe(
        Model root,
        Map<String, Object> workspace,
        boolean preserveLocalChanges
    ) throws Exception {
        Set<String> modified = new LinkedHashSet<String>();
        Map<String, Object> workspaceState = null;
        try {
            workspaceState = loadWorkspaceState(MiniJson.text(workspace, "id"));
        } catch (Exception ignored) {
            // Fall back to Creo's in-memory flag if the bridge cannot be queried.
        }
        if (!preserveLocalChanges && modelNeedsReloadProtection(root, workspaceState)) {
            modified.add(root.GetFileName());
        }
        for (LoadedModelInfo loaded : loadedCreoModels().values()) {
            if (loaded.model != root
                && modelNeedsReloadProtection(loaded.model, workspaceState)) {
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

    private static boolean modelNeedsReloadProtection(
        Model model,
        Map<String, Object> workspaceState
    ) {
        if (model == null) return false;
        if (workspaceState == null) {
            try { return model.GetIsModified(); }
            catch (Throwable ignored) { return false; }
        }
        String logical = logicalCreoFileName(safeFileName(model));
        for (Object raw : MiniJson.array(workspaceState.get("local_files"))) {
            Map<String, Object> file = MiniJson.object(raw);
            String candidate = MiniJson.text(file, "logical_file_name");
            if (logical.equalsIgnoreCase(candidate)) {
                // Creo can report a model dirty after loading dependencies even
                // when the workspace bytes still match the Nexus baseline.
                return MiniJson.bool(file, "modified");
            }
        }
        try { return model.GetIsModified(); }
        catch (Throwable ignored) { return false; }
    }

    private static String safeFileName(Model model) {
        try { return model == null ? "" : model.GetFileName(); }
        catch (Throwable ignored) { return ""; }
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
        // Creo can expose feature and ribbon commands only after the first model
        // is retrieved. Retry registration at that point before the user edits.
        installCommandGuards();
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
        activateModel(model);
        return model;
    }

    private static boolean modelMatchesWorkspace(
        Model model,
        String workspacePath,
        String logicalFileName
    ) {
        if (model == null || !modelIsInsideWorkspace(model, workspacePath)) {
            return false;
        }
        return logicalCreoFileName(safeFileName(model)).equalsIgnoreCase(
            logicalCreoFileName(logicalFileName)
        );
    }

    private static boolean modelIsInsideWorkspace(Model model, String workspacePath) {
        if (model == null || workspacePath == null || workspacePath.trim().length() == 0) {
            return false;
        }
        try {
            String origin = model.GetOrigin();
            if (origin == null || origin.trim().length() == 0) return false;
            String root = new File(workspacePath).getCanonicalPath();
            String candidate = new File(origin).getCanonicalPath();
            return candidate.equalsIgnoreCase(root)
                || candidate.toLowerCase().startsWith((root + File.separator).toLowerCase());
        } catch (Throwable ignored) {
            return false;
        }
    }

    private static void activateModel(Model model) throws Exception {
        if (model == null) {
            throw new IllegalStateException("Creo returned an empty model.");
        }
        model.Display();
        Window window = session.GetModelWindow(model);
        if (window == null) {
            window = session.CreateModelWindow(model);
        }
        if (window != null) {
            window.Activate();
            session.SetCurrentWindow(window);
            window.Refresh();
        }
        session.FlushCurrentWindow();
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
        final UICommandBracketListener listener;

        CommandGuardRegistration(UICommand command, UICommandBracketListener listener) {
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
