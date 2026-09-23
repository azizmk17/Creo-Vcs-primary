import com.ptc.cipjava.jxthrowable;
import com.ptc.pfc.pfcCommand.DefaultUICommandBracketListener;
import com.ptc.pfc.pfcExceptions.XCancelProEAction;

/** Intercepts Creo edit commands before a managed read-only model changes. */
public class NexusEditGuard extends DefaultUICommandBracketListener {
    public void OnBeforeCommand() throws jxthrowable {
        try {
            if (!NexusJLink.allowModelEdit()) {
                XCancelProEAction.Throw();
            }
        } catch (jxthrowable error) {
            throw error;
        } catch (Throwable error) {
            NexusDialogs.error(
                error.getMessage() == null ? String.valueOf(error) : error.getMessage(),
                "Nexus Edit Conflict"
            );
            XCancelProEAction.Throw();
        }
    }

    public void OnAfterCommand() throws jxthrowable {
        try {
            NexusJLink.checkForUnauthorizedModifications();
        } catch (Throwable error) {
            NexusDialogs.error(
                error.getMessage() == null ? String.valueOf(error) : error.getMessage(),
                "Nexus Modification Conflict"
            );
        }
    }
}
