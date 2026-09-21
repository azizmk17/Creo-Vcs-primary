import java.awt.Component;
import javax.swing.JComboBox;
import javax.swing.JDialog;
import javax.swing.JOptionPane;
import javax.swing.JTextField;
import javax.swing.SwingUtilities;

/** Always-on-top Swing dialogs for the separate synchronous J-Link JVM. */
public final class NexusDialogs {
    private NexusDialogs() {
    }

    public static void info(String message, String title) {
        showMessage(message, title, JOptionPane.INFORMATION_MESSAGE);
    }

    public static void warning(String message, String title) {
        showMessage(message, title, JOptionPane.WARNING_MESSAGE);
    }

    public static void error(String message, String title) {
        showMessage(message, title, JOptionPane.ERROR_MESSAGE);
    }

    public static void warningLater(final String message, final String title) {
        SwingUtilities.invokeLater(new Runnable() {
            public void run() {
                warning(message, title);
            }
        });
    }

    public static boolean confirm(String message, String title, int messageType) {
        JOptionPane pane = new JOptionPane(
            message,
            messageType,
            JOptionPane.YES_NO_OPTION
        );
        Object value = show(pane, title);
        return value instanceof Integer
            && ((Integer) value).intValue() == JOptionPane.YES_OPTION;
    }

    public static String input(String message, String title) {
        final JTextField field = new JTextField(34);
        JOptionPane pane = new JOptionPane(
            new Object[] {message, field},
            JOptionPane.QUESTION_MESSAGE,
            JOptionPane.OK_CANCEL_OPTION
        );
        Object value = show(pane, title, new Runnable() {
            public void run() {
                field.requestFocusInWindow();
            }
        });
        if (!(value instanceof Integer)
            || ((Integer) value).intValue() != JOptionPane.OK_OPTION) {
            return null;
        }
        return field.getText();
    }

    public static Object choose(
        String message,
        String title,
        Object[] values,
        Object initialValue
    ) {
        final JComboBox<Object> choices = new JComboBox<Object>(values);
        choices.setSelectedItem(initialValue);
        JOptionPane pane = new JOptionPane(
            new Object[] {message, choices},
            JOptionPane.QUESTION_MESSAGE,
            JOptionPane.OK_CANCEL_OPTION
        );
        Object value = show(pane, title, new Runnable() {
            public void run() {
                choices.requestFocusInWindow();
            }
        });
        if (!(value instanceof Integer)
            || ((Integer) value).intValue() != JOptionPane.OK_OPTION) {
            return null;
        }
        return choices.getSelectedItem();
    }

    private static void showMessage(String message, String title, int messageType) {
        JOptionPane pane = new JOptionPane(
            message,
            messageType,
            JOptionPane.DEFAULT_OPTION
        );
        show(pane, title);
    }

    private static Object show(JOptionPane pane, String title) {
        return show(pane, title, null);
    }

    private static Object show(
        final JOptionPane pane,
        final String title,
        final Runnable afterVisible
    ) {
        if (SwingUtilities.isEventDispatchThread()) {
            return showOnEventThread(pane, title, afterVisible);
        }
        final Object[] value = new Object[1];
        final RuntimeException[] failure = new RuntimeException[1];
        try {
            SwingUtilities.invokeAndWait(new Runnable() {
                public void run() {
                    try {
                        value[0] = showOnEventThread(pane, title, afterVisible);
                    } catch (RuntimeException error) {
                        failure[0] = error;
                    }
                }
            });
        } catch (Exception error) {
            throw new IllegalStateException("Could not display the Nexus dialog.", error);
        }
        if (failure[0] != null) {
            throw failure[0];
        }
        return value[0];
    }

    private static Object showOnEventThread(
        JOptionPane pane,
        String title,
        final Runnable afterVisible
    ) {
        final JDialog dialog = pane.createDialog((Component) null, title);
        dialog.setAlwaysOnTop(true);
        dialog.setAutoRequestFocus(true);
        dialog.setModal(true);
        dialog.setDefaultCloseOperation(JDialog.DISPOSE_ON_CLOSE);
        if (afterVisible != null) {
            SwingUtilities.invokeLater(afterVisible);
        }
        dialog.toFront();
        dialog.requestFocus();
        dialog.setVisible(true);
        Object value = pane.getValue();
        dialog.dispose();
        return value;
    }
}
