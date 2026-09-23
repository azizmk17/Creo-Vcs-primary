import java.awt.Component;
import java.awt.BorderLayout;
import java.awt.Color;
import java.awt.Dimension;
import java.awt.Font;
import java.awt.Graphics;
import java.awt.Graphics2D;
import java.awt.GridLayout;
import java.awt.FlowLayout;
import java.awt.RenderingHints;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import javax.swing.AbstractCellEditor;
import javax.swing.BorderFactory;
import javax.swing.Box;
import javax.swing.BoxLayout;
import javax.swing.Icon;
import javax.swing.JComboBox;
import javax.swing.DefaultCellEditor;
import javax.swing.JDialog;
import javax.swing.JLabel;
import javax.swing.JTable;
import javax.swing.JOptionPane;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
import javax.swing.JTextArea;
import javax.swing.JTextField;
import javax.swing.ListSelectionModel;
import javax.swing.SwingUtilities;
import javax.swing.event.ListSelectionEvent;
import javax.swing.event.ListSelectionListener;
import javax.swing.table.AbstractTableModel;
import javax.swing.table.TableCellEditor;
import javax.swing.table.TableCellRenderer;

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

    public static ConflictResult conflicts(
        String title,
        ConflictItem[] items
    ) {
        if (items == null || items.length == 0) {
            return new ConflictResult(new LinkedHashMap<String, String>());
        }
        final ConflictTableModel model = new ConflictTableModel(items);
        final JTable table = new JTable(model);
        table.setRowHeight(28);
        table.setSelectionMode(ListSelectionModel.MULTIPLE_INTERVAL_SELECTION);
        table.setRowSelectionAllowed(true);
        table.setColumnSelectionAllowed(false);
        table.setShowGrid(true);
        table.setGridColor(new Color(205, 211, 216));
        table.setFillsViewportHeight(true);
        table.getTableHeader().setReorderingAllowed(false);
        table.getColumnModel().getColumn(0).setPreferredWidth(235);
        table.getColumnModel().getColumn(1).setPreferredWidth(430);
        table.getColumnModel().getColumn(2).setPreferredWidth(210);
        table.getColumnModel().getColumn(3).setPreferredWidth(180);
        table.getColumnModel().getColumn(1).setCellRenderer(
            new ConflictDescriptionRenderer(model)
        );
        table.getColumnModel().getColumn(2).setCellRenderer(
            new ConflictActionRenderer(model)
        );
        table.getColumnModel().getColumn(2).setCellEditor(
            new ConflictActionEditor(model)
        );

        final JTextArea selectedDescription = new JTextArea(5, 84);
        selectedDescription.setEditable(false);
        selectedDescription.setLineWrap(true);
        selectedDescription.setWrapStyleWord(true);
        selectedDescription.setBackground(new Color(248, 249, 250));
        selectedDescription.setBorder(BorderFactory.createEmptyBorder(7, 9, 7, 9));
        JPanel descriptionPanel = new JPanel(new BorderLayout());
        descriptionPanel.setBorder(
            BorderFactory.createTitledBorder("Selected Conflict Description")
        );
        descriptionPanel.add(new JScrollPane(selectedDescription), BorderLayout.CENTER);
        descriptionPanel.setPreferredSize(new Dimension(1010, 125));

        table.getSelectionModel().addListSelectionListener(new ListSelectionListener() {
            public void valueChanged(ListSelectionEvent event) {
                if (event.getValueIsAdjusting()) return;
                int[] rows = table.getSelectedRows();
                StringBuilder text = new StringBuilder();
                if (rows.length > 1) {
                    text.append(rows.length).append(" conflicts selected.\n\n");
                }
                for (int index = 0; index < rows.length; index++) {
                    ConflictItem item = model.itemAt(rows[index]);
                    if (index > 0) text.append('\n');
                    if (rows.length > 1) text.append(item.object).append(": ");
                    text.append(item.description);
                    if (item.context.length() > 0) {
                        text.append("\n").append(item.context);
                    }
                }
                selectedDescription.setText(text.toString());
                selectedDescription.setCaretPosition(0);
            }
        });
        table.setRowSelectionInterval(0, 0);

        final JComboBox<ConflictAction> setSelected = new JComboBox<ConflictAction>();
        final JComboBox<ConflictAction> setAll = new JComboBox<ConflictAction>();
        ConflictAction selectedPlaceholder = new ConflictAction("", "Set Selected");
        ConflictAction allPlaceholder = new ConflictAction("", "Set All");
        setSelected.addItem(selectedPlaceholder);
        setAll.addItem(allPlaceholder);
        for (ConflictAction action : model.availableActions()) {
            setSelected.addItem(action);
            setAll.addItem(action);
        }
        setSelected.addActionListener(new java.awt.event.ActionListener() {
            public void actionPerformed(java.awt.event.ActionEvent event) {
                ConflictAction action = (ConflictAction) setSelected.getSelectedItem();
                if (action != null && action.code.length() > 0) {
                    model.setRows(table.getSelectedRows(), action.code);
                    setSelected.setSelectedIndex(0);
                }
            }
        });
        setAll.addActionListener(new java.awt.event.ActionListener() {
            public void actionPerformed(java.awt.event.ActionEvent event) {
                ConflictAction action = (ConflictAction) setAll.getSelectedItem();
                if (action != null && action.code.length() > 0) {
                    model.setAll(action.code);
                    setAll.setSelectedIndex(0);
                }
            }
        });

        JLabel count = new JLabel(
            items.length + (items.length == 1 ? " Conflict" : " Conflicts")
        );
        count.setFont(count.getFont().deriveFont(Font.BOLD));
        JPanel controls = new JPanel(new BorderLayout());
        controls.add(count, BorderLayout.WEST);
        JPanel actions = new JPanel(new FlowLayout(FlowLayout.RIGHT, 6, 0));
        actions.add(setSelected);
        actions.add(setAll);
        controls.add(actions, BorderLayout.EAST);

        JScrollPane scroll = new JScrollPane(table);
        scroll.setPreferredSize(new Dimension(1010, 300));
        scroll.setBorder(BorderFactory.createEtchedBorder());
        JPanel panel = new JPanel(new BorderLayout(0, 8));
        panel.add(controls, BorderLayout.NORTH);
        panel.add(scroll, BorderLayout.CENTER);
        panel.add(descriptionPanel, BorderLayout.SOUTH);

        JOptionPane pane = new JOptionPane(
            panel,
            JOptionPane.PLAIN_MESSAGE,
            JOptionPane.OK_CANCEL_OPTION
        );
        Object value = show(pane, title);
        if (!(value instanceof Integer)
            || ((Integer) value).intValue() != JOptionPane.OK_OPTION) {
            return null;
        }
        if (table.isEditing()) table.getCellEditor().stopCellEditing();
        Map<String, String> resolutions = new LinkedHashMap<String, String>();
        for (int row = 0; row < model.getRowCount(); row++) {
            resolutions.put(model.itemAt(row).id, model.actionAt(row));
        }
        return new ConflictResult(resolutions);
    }

    public static ChecklistResult checklist(
        String message,
        String title,
        Object[] values,
        boolean[] checked,
        String noteLabel
    ) {
        ChecklistItem[] items = new ChecklistItem[values.length];
        int readyCount = 0;
        int modifiedCount = 0;
        int blockedCount = 0;
        for (int index = 0; index < values.length; index++) {
            items[index] = values[index] instanceof ChecklistItem
                ? (ChecklistItem) values[index]
                : new ChecklistItem(
                    String.valueOf(values[index]), "", ChecklistItem.READY, true
                );
            ChecklistItem item = items[index];
            if (ChecklistItem.BLOCKED.equals(item.status)) blockedCount++;
            else if (ChecklistItem.MODIFIED.equals(item.status)) modifiedCount++;
            else readyCount++;
        }

        final ChecklistTableModel model = new ChecklistTableModel(items, checked);
        final JTable table = new JTable(model);
        table.setRowHeight(27);
        table.setSelectionMode(ListSelectionModel.MULTIPLE_INTERVAL_SELECTION);
        table.setRowSelectionAllowed(true);
        table.setColumnSelectionAllowed(false);
        table.setShowGrid(true);
        table.setGridColor(new Color(205, 211, 216));
        table.setFillsViewportHeight(true);
        table.getTableHeader().setReorderingAllowed(false);
        table.getColumnModel().getColumn(0).setPreferredWidth(250);
        table.getColumnModel().getColumn(1).setPreferredWidth(390);
        table.getColumnModel().getColumn(2).setPreferredWidth(135);
        table.getColumnModel().getColumn(3).setPreferredWidth(180);
        table.getColumnModel().getColumn(0).setCellRenderer(new ChecklistObjectRenderer());
        table.getColumnModel().getColumn(2).setCellRenderer(new ChecklistActionRenderer());
        table.getColumnModel().getColumn(2).setCellEditor(
            new DefaultCellEditor(new JComboBox<String>(new String[] {"Check in", "Skip"}))
        );
        JScrollPane scroll = new JScrollPane(table);
        scroll.setBorder(BorderFactory.createEtchedBorder());
        scroll.setPreferredSize(new Dimension(920, 270));

        JLabel heading = new JLabel(message);
        heading.setFont(heading.getFont().deriveFont(Font.BOLD));
        JLabel count = new JLabel(
            values.length + (values.length == 1 ? " CAD Document" : " CAD Documents")
                + "    Ready: " + readyCount
                + "    Modified: " + modifiedCount
                + "    Blocked: " + blockedCount
        );
        count.setForeground(new Color(78, 86, 94));
        final JComboBox<String> setAll = new JComboBox<String>(
            new String[] {"Set All", "Check in", "Skip"}
        );
        setAll.addActionListener(new java.awt.event.ActionListener() {
            public void actionPerformed(java.awt.event.ActionEvent event) {
                String action = String.valueOf(setAll.getSelectedItem());
                if ("Check in".equals(action) || "Skip".equals(action)) {
                    model.setAll(action);
                    setAll.setSelectedIndex(0);
                }
            }
        });
        final JComboBox<String> setSelected = new JComboBox<String>(
            new String[] {"Set Selected", "Check in", "Skip"}
        );
        setSelected.addActionListener(new java.awt.event.ActionListener() {
            public void actionPerformed(java.awt.event.ActionEvent event) {
                String action = String.valueOf(setSelected.getSelectedItem());
                if ("Check in".equals(action) || "Skip".equals(action)) {
                    model.setRows(table.getSelectedRows(), action);
                    setSelected.setSelectedIndex(0);
                }
            }
        });
        JPanel controls = new JPanel(new BorderLayout());
        controls.add(count, BorderLayout.WEST);
        JPanel setAllPanel = new JPanel(new FlowLayout(FlowLayout.RIGHT, 6, 0));
        setAllPanel.add(setSelected);
        setAllPanel.add(setAll);
        controls.add(setAllPanel, BorderLayout.EAST);
        JPanel header = new JPanel(new GridLayout(2, 1, 0, 5));
        header.add(heading);
        header.add(controls);

        final JTextArea selectedDescription = new JTextArea(4, 80);
        selectedDescription.setEditable(false);
        selectedDescription.setLineWrap(true);
        selectedDescription.setWrapStyleWord(true);
        selectedDescription.setBackground(new Color(248, 249, 250));
        selectedDescription.setBorder(BorderFactory.createEmptyBorder(6, 8, 6, 8));
        JPanel descriptionPanel = new JPanel(new BorderLayout());
        descriptionPanel.setBorder(
            BorderFactory.createTitledBorder("Selected CAD Document Description")
        );
        descriptionPanel.add(new JScrollPane(selectedDescription), BorderLayout.CENTER);
        descriptionPanel.setPreferredSize(new Dimension(920, 105));
        table.getSelectionModel().addListSelectionListener(new ListSelectionListener() {
            public void valueChanged(ListSelectionEvent event) {
                if (event.getValueIsAdjusting()) return;
                int[] rows = table.getSelectedRows();
                if (rows.length == 0) {
                    selectedDescription.setText("");
                    return;
                }
                StringBuilder description = new StringBuilder();
                if (rows.length > 1) {
                    description.append(rows.length).append(" CAD Documents selected.\n\n");
                }
                for (int index = 0; index < rows.length; index++) {
                    ChecklistItem item = model.itemAt(rows[index]);
                    if (index > 0) description.append('\n');
                    if (rows.length > 1) description.append(item.primary).append(": ");
                    description.append(
                        item.tooltip.length() > 0 ? item.tooltip : item.secondary
                    );
                }
                selectedDescription.setText(description.toString());
                selectedDescription.setCaretPosition(0);
            }
        });
        if (values.length > 0) table.setRowSelectionInterval(0, 0);

        final JTextField note = new JTextField(54);
        JPanel notePanel = new JPanel(new GridLayout(2, 1, 0, 4));
        notePanel.add(new javax.swing.JLabel(noteLabel));
        notePanel.add(note);

        JPanel panel = new JPanel(new BorderLayout(0, 8));
        panel.add(header, BorderLayout.NORTH);
        panel.add(scroll, BorderLayout.CENTER);
        Box bottom = Box.createVerticalBox();
        bottom.add(descriptionPanel);
        bottom.add(Box.createVerticalStrut(8));
        bottom.add(notePanel);
        panel.add(bottom, BorderLayout.SOUTH);

        JOptionPane pane = new JOptionPane(
            panel,
            JOptionPane.PLAIN_MESSAGE,
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
        if (table.isEditing()) {
            table.getCellEditor().stopCellEditing();
        }
        List<Integer> selected = new ArrayList<Integer>();
        for (int index = 0; index < model.getRowCount(); index++) {
            if ("Check in".equals(model.actionAt(index))) {
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

    public static final class ConflictResult {
        private final Map<String, String> actions;

        private ConflictResult(Map<String, String> actions) {
            this.actions = actions;
        }

        public String actionFor(String conflictId) {
            String value = actions.get(conflictId);
            return value == null ? "CANCEL" : value;
        }
    }

    public static final class ConflictItem {
        private final String id;
        private final String object;
        private final String description;
        private final String name;
        private final String context;
        private final String severity;
        private final ConflictAction[] actions;
        private final String defaultAction;

        public ConflictItem(
            String id,
            String object,
            String description,
            String name,
            String context,
            String severity,
            String[] actionCodes,
            String[] actionLabels,
            String defaultAction
        ) {
            this.id = id == null ? "" : id;
            this.object = object == null ? "" : object;
            this.description = description == null ? "" : description;
            this.name = name == null ? "" : name;
            this.context = context == null ? "" : context;
            this.severity = severity == null ? "OVERRIDABLE" : severity;
            int size = actionCodes == null ? 0 : actionCodes.length;
            this.actions = new ConflictAction[size];
            for (int index = 0; index < size; index++) {
                String label = actionLabels != null && index < actionLabels.length
                    ? actionLabels[index]
                    : actionCodes[index];
                this.actions[index] = new ConflictAction(actionCodes[index], label);
            }
            this.defaultAction = supports(defaultAction)
                ? defaultAction
                : size > 0 ? this.actions[0].code : "CANCEL";
        }

        private boolean supports(String actionCode) {
            if (actionCode == null) return false;
            for (ConflictAction action : actions) {
                if (action.code.equals(actionCode)) return true;
            }
            return false;
        }

        private String labelFor(String actionCode) {
            for (ConflictAction action : actions) {
                if (action.code.equals(actionCode)) return action.label;
            }
            return actionCode == null ? "" : actionCode;
        }
    }

    private static final class ConflictAction {
        private final String code;
        private final String label;

        private ConflictAction(String code, String label) {
            this.code = code == null ? "" : code;
            this.label = label == null ? "" : label;
        }

        public String toString() { return label; }
    }

    private static final class ConflictTableModel extends AbstractTableModel {
        private static final long serialVersionUID = 1L;
        private final ConflictItem[] items;
        private final String[] selectedActions;
        private final String[] columns = new String[] {
            "Object", "Description", "Action", "Name"
        };

        private ConflictTableModel(ConflictItem[] items) {
            this.items = items;
            this.selectedActions = new String[items.length];
            for (int index = 0; index < items.length; index++) {
                selectedActions[index] = items[index].defaultAction;
            }
        }

        public int getRowCount() { return items.length; }
        public int getColumnCount() { return columns.length; }
        public String getColumnName(int column) { return columns[column]; }
        public boolean isCellEditable(int row, int column) {
            return column == 2 && items[row].actions.length > 0;
        }

        public Object getValueAt(int row, int column) {
            ConflictItem item = items[row];
            if (column == 0) return item.object;
            if (column == 1) return item.description;
            if (column == 2) return selectedActions[row];
            return item.name;
        }

        public void setValueAt(Object value, int row, int column) {
            if (column != 2) return;
            String action = value instanceof ConflictAction
                ? ((ConflictAction) value).code
                : String.valueOf(value);
            if (items[row].supports(action)) {
                selectedActions[row] = action;
                fireTableCellUpdated(row, column);
            }
        }

        private ConflictItem itemAt(int row) { return items[row]; }
        private String actionAt(int row) { return selectedActions[row]; }

        private List<ConflictAction> availableActions() {
            Map<String, ConflictAction> byCode =
                new LinkedHashMap<String, ConflictAction>();
            for (ConflictItem item : items) {
                for (ConflictAction action : item.actions) {
                    if (!byCode.containsKey(action.code)) {
                        byCode.put(action.code, action);
                    }
                }
            }
            return new ArrayList<ConflictAction>(byCode.values());
        }

        private void setRows(int[] rows, String action) {
            for (int index = 0; index < rows.length; index++) {
                int row = rows[index];
                if (row >= 0 && row < items.length && items[row].supports(action)) {
                    selectedActions[row] = action;
                }
            }
            fireTableDataChanged();
        }

        private void setAll(String action) {
            for (int row = 0; row < items.length; row++) {
                if (items[row].supports(action)) selectedActions[row] = action;
            }
            fireTableDataChanged();
        }
    }

    private static final class ConflictDescriptionRenderer extends JLabel
        implements TableCellRenderer {
        private static final long serialVersionUID = 1L;
        private final ConflictTableModel model;

        private ConflictDescriptionRenderer(ConflictTableModel model) {
            this.model = model;
            setOpaque(true);
            setBorder(BorderFactory.createEmptyBorder(0, 5, 0, 5));
        }

        public Component getTableCellRendererComponent(
            JTable table,
            Object value,
            boolean selected,
            boolean focused,
            int row,
            int column
        ) {
            ConflictItem item = model.itemAt(row);
            setText(String.valueOf(value));
            setIcon(new ConflictStatusIcon(item.severity));
            setIconTextGap(6);
            setForeground(selected ? table.getSelectionForeground() : table.getForeground());
            setBackground(selected ? table.getSelectionBackground() : table.getBackground());
            setToolTipText(item.description);
            return this;
        }
    }

    private static final class ConflictActionRenderer extends JComboBox<String>
        implements TableCellRenderer {
        private static final long serialVersionUID = 1L;
        private final ConflictTableModel model;

        private ConflictActionRenderer(ConflictTableModel model) {
            this.model = model;
        }

        public Component getTableCellRendererComponent(
            JTable table,
            Object value,
            boolean selected,
            boolean focused,
            int row,
            int column
        ) {
            removeAllItems();
            ConflictItem item = model.itemAt(row);
            for (ConflictAction action : item.actions) addItem(action.label);
            setSelectedItem(item.labelFor(String.valueOf(value)));
            return this;
        }
    }

    private static final class ConflictActionEditor extends AbstractCellEditor
        implements TableCellEditor {
        private static final long serialVersionUID = 1L;
        private final ConflictTableModel model;
        private final JComboBox<ConflictAction> choices =
            new JComboBox<ConflictAction>();
        private boolean loading;

        private ConflictActionEditor(ConflictTableModel model) {
            this.model = model;
            choices.addActionListener(new java.awt.event.ActionListener() {
                public void actionPerformed(java.awt.event.ActionEvent event) {
                    if (!loading && choices.getSelectedItem() != null) {
                        stopCellEditing();
                    }
                }
            });
        }

        public Object getCellEditorValue() {
            return choices.getSelectedItem();
        }

        public Component getTableCellEditorComponent(
            JTable table,
            Object value,
            boolean selected,
            int row,
            int column
        ) {
            loading = true;
            choices.removeAllItems();
            ConflictItem item = model.itemAt(row);
            for (ConflictAction action : item.actions) choices.addItem(action);
            for (int index = 0; index < choices.getItemCount(); index++) {
                if (choices.getItemAt(index).code.equals(String.valueOf(value))) {
                    choices.setSelectedIndex(index);
                    break;
                }
            }
            loading = false;
            return choices;
        }
    }

    private static final class ConflictStatusIcon implements Icon {
        private final String severity;

        private ConflictStatusIcon(String severity) {
            this.severity = severity == null ? "OVERRIDABLE" : severity;
        }

        public int getIconWidth() { return 18; }
        public int getIconHeight() { return 18; }

        public void paintIcon(Component component, Graphics graphics, int x, int y) {
            Graphics2D g = (Graphics2D) graphics.create();
            try {
                g.setRenderingHint(
                    RenderingHints.KEY_ANTIALIASING,
                    RenderingHints.VALUE_ANTIALIAS_ON
                );
                boolean blocking = "BLOCKING".equalsIgnoreCase(severity);
                g.setColor(blocking ? new Color(184, 54, 54) : new Color(211, 130, 20));
                g.fillOval(x + 1, y + 1, 16, 16);
                g.setColor(Color.WHITE);
                g.setStroke(new java.awt.BasicStroke(2.0f));
                if (blocking) {
                    g.drawLine(x + 5, y + 5, x + 13, y + 13);
                    g.drawLine(x + 13, y + 5, x + 5, y + 13);
                } else {
                    g.drawLine(x + 9, y + 4, x + 9, y + 10);
                    g.fillOval(x + 8, y + 13, 3, 3);
                }
            } finally {
                g.dispose();
            }
        }
    }

    private static final class ChecklistTableModel extends AbstractTableModel {
        private static final long serialVersionUID = 1L;
        private final ChecklistItem[] items;
        private final String[] actions;
        private final String[] columns = new String[] {
            "Object", "Description", "Action", "Context"
        };

        private ChecklistTableModel(ChecklistItem[] items, boolean[] checked) {
            this.items = items;
            this.actions = new String[items.length];
            for (int index = 0; index < items.length; index++) {
                boolean selected = items[index].selectable
                    && checked != null
                    && index < checked.length
                    && checked[index];
                actions[index] = items[index].selectable
                    ? selected ? "Check in" : "Skip"
                    : "Blocked";
            }
        }

        public int getRowCount() { return items.length; }
        public int getColumnCount() { return columns.length; }
        public String getColumnName(int column) { return columns[column]; }

        public Object getValueAt(int row, int column) {
            ChecklistItem item = items[row];
            if (column == 0) return item;
            if (column == 1) return item.secondary;
            if (column == 2) return actions[row];
            return item.context;
        }

        public boolean isCellEditable(int row, int column) {
            return column == 2 && items[row].selectable;
        }

        public void setValueAt(Object value, int row, int column) {
            if (column != 2 || !items[row].selectable) return;
            String action = String.valueOf(value);
            if (!"Check in".equals(action) && !"Skip".equals(action)) return;
            actions[row] = action;
            fireTableCellUpdated(row, column);
        }

        private ChecklistItem itemAt(int row) { return items[row]; }
        private String actionAt(int row) { return actions[row]; }

        private void setAll(String action) {
            for (int index = 0; index < items.length; index++) {
                if (items[index].selectable) actions[index] = action;
            }
            fireTableDataChanged();
        }

        private void setRows(int[] rows, String action) {
            for (int index = 0; index < rows.length; index++) {
                int row = rows[index];
                if (row >= 0 && row < items.length && items[row].selectable) {
                    actions[row] = action;
                }
            }
            fireTableRowsUpdated(0, Math.max(0, items.length - 1));
        }
    }

    private static final class ChecklistObjectRenderer extends JLabel
        implements TableCellRenderer {
        private static final long serialVersionUID = 1L;
        private ChecklistObjectRenderer() {
            setOpaque(true);
            setBorder(BorderFactory.createEmptyBorder(0, 6, 0, 5));
        }

        public Component getTableCellRendererComponent(
            JTable table,
            Object value,
            boolean selected,
            boolean focused,
            int row,
            int column
        ) {
            ChecklistItem item = (ChecklistItem) value;
            setText(item.primary);
            setIcon(new StatusIcon(item.status));
            setIconTextGap(7);
            setFont(table.getFont().deriveFont(Font.BOLD));
            setForeground(selected ? table.getSelectionForeground() : table.getForeground());
            setBackground(selected ? table.getSelectionBackground() : table.getBackground());
            setToolTipText(item.tooltip);
            return this;
        }
    }

    private static final class ChecklistActionRenderer extends JComboBox<String>
        implements TableCellRenderer {
        private static final long serialVersionUID = 1L;
        public Component getTableCellRendererComponent(
            JTable table,
            Object value,
            boolean selected,
            boolean focused,
            int row,
            int column
        ) {
            removeAllItems();
            String action = String.valueOf(value);
            if ("Blocked".equals(action)) {
                addItem("Blocked");
                setEnabled(false);
            } else {
                addItem("Check in");
                addItem("Skip");
                setEnabled(true);
            }
            setSelectedItem(action);
            return this;
        }
    }

    public static final class ChecklistItem {
        public static final String READY = "READY";
        public static final String MODIFIED = "MODIFIED";
        public static final String BLOCKED = "BLOCKED";

        private final String primary;
        private final String secondary;
        private final String status;
        private final boolean selectable;
        private final String tooltip;
        private final String context;

        public ChecklistItem(
            String primary, String secondary, String status, boolean selectable
        ) {
            this(primary, secondary, status, selectable, secondary, "");
        }

        public ChecklistItem(
            String primary,
            String secondary,
            String status,
            boolean selectable,
            String tooltip
        ) {
            this(primary, secondary, status, selectable, tooltip, "");
        }

        public ChecklistItem(
            String primary,
            String secondary,
            String status,
            boolean selectable,
            String tooltip,
            String context
        ) {
            this.primary = primary == null ? "" : primary;
            this.secondary = secondary == null ? "" : secondary;
            this.status = status == null ? READY : status;
            this.selectable = selectable;
            this.tooltip = tooltip == null ? "" : tooltip;
            this.context = context == null ? "" : context;
        }
    }

    private static final class StatusIcon implements Icon {
        private final String status;

        private StatusIcon(String status) {
            this.status = status;
        }

        public int getIconWidth() { return 20; }
        public int getIconHeight() { return 20; }

        public void paintIcon(Component component, Graphics graphics, int x, int y) {
            Graphics2D g = (Graphics2D) graphics.create();
            try {
                g.setRenderingHint(
                    RenderingHints.KEY_ANTIALIASING,
                    RenderingHints.VALUE_ANTIALIAS_ON
                );
                Color color = ChecklistItem.BLOCKED.equals(status)
                    ? new Color(180, 58, 58)
                    : ChecklistItem.MODIFIED.equals(status)
                        ? new Color(196, 132, 28)
                        : new Color(45, 132, 82);
                g.setColor(color);
                g.fillOval(x + 1, y + 1, 18, 18);
                g.setColor(Color.WHITE);
                g.setStroke(new java.awt.BasicStroke(2.0f));
                if (ChecklistItem.BLOCKED.equals(status)) {
                    g.drawLine(x + 6, y + 6, x + 14, y + 14);
                    g.drawLine(x + 14, y + 6, x + 6, y + 14);
                } else if (ChecklistItem.MODIFIED.equals(status)) {
                    g.drawLine(x + 10, y + 5, x + 10, y + 11);
                    g.fillOval(x + 9, y + 14, 3, 3);
                } else {
                    g.drawLine(x + 5, y + 10, x + 9, y + 14);
                    g.drawLine(x + 9, y + 14, x + 15, y + 6);
                }
            } finally {
                g.dispose();
            }
        }
    }
}
