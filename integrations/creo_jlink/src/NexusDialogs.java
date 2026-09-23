import java.awt.Component;
import java.awt.BorderLayout;
import java.awt.GridLayout;
import java.util.ArrayList;
import java.util.List;
import javax.swing.BorderFactory;
import javax.swing.Box;
import javax.swing.BoxLayout;
import javax.swing.JCheckBox;
import javax.swing.JComboBox;
import javax.swing.JDialog;
import javax.swing.JOptionPane;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
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

    /** Return 0 for checkout, 1 for local intent, and -1 for cancel. */
    public static int editChoice(String message, String title) {
        Object[] options = new Object[] {
            "Check Out Now", "Continue Locally", "Cancel"
        };
        JOptionPane pane = new JOptionPane(
            message,
            JOptionPane.WARNING_MESSAGE,
            JOptionPane.DEFAULT_OPTION,
            null,
            options,
            options[0]
        );
        Object value = show(pane, title);
        if (value == options[0] || (value != null && options[0].equals(value))) return 0;
        if (value == options[1] || (value != null && options[1].equals(value))) return 1;
        return -1;
    }

    /** Return 0 for replace, 1 for skip, and -1 for cancel. */
    public static int replaceChoice(String message, String title) {
        Object[] options = new Object[] {"Replace", "Skip", "Cancel"};
        JOptionPane pane = new JOptionPane(
            message,
            JOptionPane.WARNING_MESSAGE,
            JOptionPane.DEFAULT_OPTION,
            null,
            options,
            options[0]
        );
        Object value = show(pane, title);
        if (value == options[0] || (value != null && options[0].equals(value))) return 0;
        if (value == options[1] || (value != null && options[1].equals(value))) return 1;
        return -1;
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

    public static ChecklistResult checklist(
        String message,
        String title,
        Object[] values,
        boolean[] checked,
        String noteLabel
    ) {
        final JCheckBox[] boxes = new JCheckBox[values.length];
        JPanel list = new JPanel();
        list.setLayout(new BoxLayout(list, BoxLayout.Y_AXIS));
        for (int index = 0; index < values.length; index++) {
            boxes[index] = new JCheckBox(String.valueOf(values[index]));
            boxes[index].setSelected(checked != null && index < checked.length && checked[index]);
            boxes[index].setAlignmentX(Component.LEFT_ALIGNMENT);
            list.add(boxes[index]);
        }
        JScrollPane scroll = new JScrollPane(list);
        scroll.setBorder(BorderFactory.createEtchedBorder());
        scroll.setPreferredSize(new java.awt.Dimension(760, 260));

        final JTextField note = new JTextField(54);
        JPanel notePanel = new JPanel(new GridLayout(2, 1, 0, 4));
        notePanel.add(new javax.swing.JLabel(noteLabel));
        notePanel.add(note);

        JPanel panel = new JPanel(new BorderLayout(0, 8));
        panel.add(new javax.swing.JLabel(message), BorderLayout.NORTH);
        panel.add(scroll, BorderLayout.CENTER);
        Box bottom = Box.createVerticalBox();
        bottom.add(notePanel);
        panel.add(bottom, BorderLayout.SOUTH);

        JOptionPane pane = new JOptionPane(
            panel,
            JOptionPane.QUESTION_MESSAGE,
            JOptionPane.OK_CANCEL_OPTION
        );
        Object value = show(pane, title, new Runnable() {
            public void run() {
                note.requestFocusInWindow();
            }
        });
        if (!(value instanceof Integer)
            || ((Integer) value).intValue() != JOptionPane.OK_OPTION) {
            return null;
        }
        List<Integer> selected = new ArrayList<Integer>();
        for (int index = 0; index < boxes.length; index++) {
            if (boxes[index].isSelected()) {
                selected.add(Integer.valueOf(index));
            }
        }
        int[] indexes = new int[selected.size()];
        for (int index = 0; index < selected.size(); index++) {
            indexes[index] = selected.get(index).intValue();
        }
        return new ChecklistResult(indexes, note.getText());
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

    public static final class ChecklistResult {
        public final int[] selectedIndexes;
        public final String note;

        private ChecklistResult(int[] selectedIndexes, String note) {
            this.selectedIndexes = selectedIndexes;
            this.note = note == null ? "" : note;
        }
    }
}
