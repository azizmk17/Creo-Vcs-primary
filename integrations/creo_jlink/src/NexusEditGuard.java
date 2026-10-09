import com.ptc.cipjava.jxthrowable;
import com.ptc.pfc.pfcCommand.DefaultUICommandBracketListener;
import com.ptc.pfc.pfcExceptions.XCancelProEAction;

/** Intercepts Creo edit commands before a managed read-only model changes. */
public class NexusEditGuard extends DefaultUICommandBracketListener {
    public void OnBeforeCommand() throws jxthrowable {
        try {
            if (!NexusJLink.beginEditCommand()) {
                XCancelProEAction.Throw();
            }
        } catch (jxthrowable error) {
            NexusJLink.endEditCommand();
            throw error;
        } catch (Throwable error) {
            NexusJLink.endEditCommand();
            NexusDialogs.error(
                error.getMessage() == null ? String.valueOf(error) : error.getMessage(),
                "Nexus Edit Conflict"
            );
            XCancelProEAction.Throw();
        }
    }

    public void OnAfterCommand() throws jxthrowable {
        NexusJLink.endEditCommand();
        NexusJLink.refreshCommandProtection();
    }
}
