import java.awt.BasicStroke;
import java.awt.Color;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.geom.Ellipse2D;
import java.awt.geom.Path2D;
import java.awt.geom.Rectangle2D;
import java.awt.geom.RoundRectangle2D;
import java.awt.image.BufferedImage;
import java.io.File;
import javax.imageio.ImageIO;

public final class GenerateNexusIcons {
    private static final String[] COMMANDS = new String[] {
        "connect", "workspace", "retrieve", "status", "workspacestatus",
        "history", "checkout", "checkin", "undo", "revise", "release"
    };

    private GenerateNexusIcons() { }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) throw new IllegalArgumentException("Expected icon output directory");
        File directory = new File(args[0]);
        if (!directory.exists() && !directory.mkdirs()) {
            throw new IllegalStateException("Could not create icon directory: " + directory);
        }
        for (String command : COMMANDS) {
            writeIcon(directory, command, 16, false);
            writeIcon(directory, command, 32, true);
        }
    }

    private static void writeIcon(File directory, String command, int size, boolean large)
        throws Exception {
        BufferedImage image = new BufferedImage(size, size, BufferedImage.TYPE_INT_ARGB);
        Graphics2D graphics = image.createGraphics();
        graphics.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
        graphics.scale(size / 32.0, size / 32.0);
        graphics.setStroke(new BasicStroke(2.1f, BasicStroke.CAP_ROUND, BasicStroke.JOIN_ROUND));
        graphics.setColor(new Color(247, 250, 252));
        graphics.fill(new RoundRectangle2D.Double(1, 1, 30, 30, 7, 7));
        graphics.setColor(accent(command));
        drawSymbol(graphics, command);
        graphics.dispose();
        String filename = "nexus_" + command + (large ? "_large" : "") + ".png";
        ImageIO.write(image, "png", new File(directory, filename));
    }

    private static Color accent(String command) {
        if ("retrieve".equals(command) || "checkin".equals(command) || "release".equals(command))
            return new Color(30, 145, 95);
        if ("history".equals(command) || "revise".equals(command))
            return new Color(203, 112, 34);
        if ("undo".equals(command)) return new Color(185, 65, 61);
        if ("status".equals(command) || "workspacestatus".equals(command))
            return new Color(32, 121, 156);
        return new Color(42, 93, 142);
    }

    private static void drawSymbol(Graphics2D g, String command) {
        if ("connect".equals(command)) {
            g.draw(new Ellipse2D.Double(5, 10, 13, 13));
            g.draw(new Ellipse2D.Double(14, 10, 13, 13));
            g.drawLine(12, 16, 20, 16);
        } else if ("workspace".equals(command)) {
            g.draw(new RoundRectangle2D.Double(5, 9, 22, 17, 2, 2));
            g.drawLine(5, 13, 14, 13);
            g.drawLine(8, 7, 15, 7);
        } else if ("retrieve".equals(command) || "checkin".equals(command)) {
            g.drawLine(16, 5, 16, 20);
            g.drawLine(10, 14, 16, 20);
            g.drawLine(22, 14, 16, 20);
            g.drawLine(7, 25, 25, 25);
            if ("retrieve".equals(command)) g.drawLine(7, 25, 7, 22);
            else g.drawLine(25, 25, 25, 22);
        } else if ("status".equals(command)) {
            g.draw(new Ellipse2D.Double(5, 5, 22, 22));
            g.drawLine(10, 16, 14, 20);
            g.drawLine(14, 20, 22, 11);
        } else if ("workspacestatus".equals(command)) {
            g.draw(new RoundRectangle2D.Double(7, 5, 18, 23, 2, 2));
            g.drawLine(11, 11, 21, 11);
            g.drawLine(11, 16, 21, 16);
            g.drawLine(11, 21, 18, 21);
        } else if ("history".equals(command)) {
            g.draw(new Ellipse2D.Double(5, 5, 22, 22));
            g.drawLine(16, 9, 16, 16);
            g.drawLine(16, 16, 22, 19);
        } else if ("checkout".equals(command)) {
            g.draw(new RoundRectangle2D.Double(7, 14, 18, 13, 2, 2));
            g.draw(new Ellipse2D.Double(11, 6, 10, 13));
            g.setColor(new Color(247, 250, 252));
            g.fill(new Rectangle2D.Double(10, 14, 12, 5));
            g.setColor(accent(command));
            g.drawLine(16, 19, 16, 23);
        } else if ("undo".equals(command)) {
            Path2D path = new Path2D.Double();
            path.moveTo(9, 13);
            path.lineTo(4, 13);
            path.lineTo(9, 8);
            path.moveTo(5, 13);
            path.curveTo(22, 5, 29, 18, 20, 24);
            g.draw(path);
        } else if ("revise".equals(command)) {
            g.draw(new RoundRectangle2D.Double(6, 6, 16, 18, 2, 2));
            g.draw(new RoundRectangle2D.Double(10, 9, 16, 18, 2, 2));
            g.drawLine(14, 14, 22, 14);
            g.drawLine(14, 18, 22, 18);
        } else if ("release".equals(command)) {
            Path2D badge = new Path2D.Double();
            badge.moveTo(16, 4);
            badge.lineTo(20, 9);
            badge.lineTo(27, 10);
            badge.lineTo(24, 16);
            badge.lineTo(25, 23);
            badge.lineTo(18, 22);
            badge.lineTo(13, 27);
            badge.lineTo(10, 20);
            badge.lineTo(5, 16);
            badge.lineTo(10, 11);
            badge.closePath();
            g.draw(badge);
            g.drawLine(11, 16, 15, 20);
            g.drawLine(15, 20, 22, 12);
        }
    }
}
